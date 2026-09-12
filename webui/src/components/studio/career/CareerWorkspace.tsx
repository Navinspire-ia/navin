// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Customizer,
  DefaultButton,
  Icon,
  IconButton,
  MessageBar,
  MessageBarType,
  Panel,
  PanelType,
  PrimaryButton,
  ProgressIndicator,
  TextField,
  Toggle,
  createTheme,
  type IContextualMenuItem,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import type { OfferDeskOpen } from "@/components/studio/career/CareerDashboard";
import { CvPreview, DossierPreview } from "@/components/studio/DossierPreview";
import { DocumentGenerationNotice } from "@/components/studio/DocumentGenerationNotice";
import { TradingLoopSchedulePanel } from "@/components/studio/trading/TradingLoopSchedulePanel";
import type { CareerLoopSchedule } from "@/lib/career-api";
import { browserTimeZone } from "@/lib/trading-loop-schedule";
import { CareerFilters } from "@/components/studio/career/CareerFilters";
import { CareerWizard } from "@/components/studio/career/CareerWizard";
import {
  CareerMailAccountSummary, CareerMailCompose, CareerMailReceiptView, CareerMailSettings, careerMailError,
} from "@/components/studio/career/CareerMailPanel";
import {
  DEFAULT_SEARCH_COUNTRIES,
  BUTTON_STYLES,
  HEADER_BUTTON_STYLES,
  ICON_BUTTON_STYLES,
  OFFER_ROW_MENU,
  careerDeskHash,
  careerHashJob,
  careerShowsDesk,
  careerShowsWizard,
  expandSearchCountries,
  openOfficialCareerUrl,
  openToolsHash,
  replaceCareerHash,
  type CareerShellView,
} from "@/components/studio/career/career-ui";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { useThemeValue } from "@/hooks/useTheme";
import { ApiError, fetchMcpPresets } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import {
  authorizedPortals,
  emptyCareerDesk,
  fetchCareerDesk,
  fetchRemotiveOpportunities,
  isAbortError,
  postCareer,
  triggerCareerDownload,
  type CareerApplication,
  type CareerDesk,
  type CareerEmployers as CareerEmployersPayload,
  type CareerMcpHint,
  type CareerInboxItem,
  type CareerMailDraft,
  type CareerOpportunity,
  type CareerProfile,
  type CareerSource,
  type CareerTrack,
  type CareerUserEmployer,
  type OfficialPortal,
} from "@/lib/career-api";
import { CareerEmployers } from "@/components/studio/career/CareerEmployers";
import {
  classifyReply,
  dedupeApplications,
  draftFollowup,
  jobIsRelevant,
  normalizeJobUrl,
  scoreOpportunity,
  stripHtml,
} from "@/lib/career-match";
import {
  applicationStatusKey,
  followupDue,
  formatCareerDate,
  listTrackedApplications,
  type ApplicationView,
} from "@/lib/career-apps";
import { contractLabel, offerContracts, offerFacts, type OfferFact, type OfferFactCopy } from "@/lib/career-facts";
import {
  applyOfferFilter,
  emptyOfferFilter,
  offerDomain,
  offerIsoDay,
  type OfferFacetFilter,
} from "@/lib/career-filters";
import { downloadCareerExport } from "@/lib/career-export";
import {
  CAREER_PAGE_SIZE,
  activeOffers,
  archivedOffers,
  favoriteOffers,
  offerPageCount,
  offerPageOf,
  paginateOffers,
  retentionDays,
  type OfferListView,
  type OfferRowAction,
} from "@/lib/career-list";
import { fetchOfficialAtsBoards, parsePastedOffer } from "@/lib/career-portals";
import { fetchWebSearchOpportunities, isJobListingPage } from "@/lib/career-web";
import { cn } from "@/lib/utils";

const SPRING = { type: "spring" as const, duration: 0.3, bounce: 0 };
const SURFACE =
  "rounded-2xl shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 dark:outline-white/10";
const STAGE_FLOW = [
  "discovered",
  "matched",
  "ready",
  "applied",
  "replied",
  "interview",
  "offer",
  "won",
] as const;

const FREELANCE_PANES = [
  { id: "applications", icon: "Send" },
  { id: "inbox", icon: "Mail" },
  { id: "pipeline", icon: "ViewList" },
  { id: "profile", icon: "Contact" },
] as const;

const JOBS_PANES = [
  { id: "applications", icon: "Send" },
  { id: "inbox", icon: "Mail" },
  { id: "interviews", icon: "Calendar" },
  { id: "profile", icon: "Contact" },
] as const;

/** The desk lands on Offers: there is no dashboard pane any more. */
type Pane = "offers" | "applications" | "inbox" | "pipeline" | "interviews" | "profile";
type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

function useCareerTheme(mode: "light" | "dark") {
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

function joinList(value: string[] | undefined): string {
  return (value || []).join(", ");
}

function opportunityKey(row: CareerOpportunity): string {
  return normalizeJobUrl(row.url || "") || row.id;
}

/** The desk API travels in request headers, so ingest sends short rows only. */
function compactHit(row: CareerOpportunity): Record<string, unknown> {
  return {
    id: row.id,
    source: row.source,
    title: row.title,
    company: row.company || "",
    location: row.location || "",
    country: row.country || "",
    url: row.url || "",
    remote: row.remote || "",
    track: row.track || "",
    compensation: row.compensation ?? null,
    currency: row.currency || "",
    stack: (row.stack || []).slice(0, 12),
    description: (row.description || "").slice(0, 600),
  };
}

function scoreTone(score: number | null | undefined): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 80) return "text-indigo-700 dark:text-indigo-300";
  if (score < 50) return "text-rose-600 dark:text-rose-400";
  return "text-amber-700 dark:text-amber-300";
}

function nextStage(stage: string): string | null {
  const index = STAGE_FLOW.indexOf(stage as (typeof STAGE_FLOW)[number]);
  if (index < 0 || index >= STAGE_FLOW.length - 1) return null;
  return STAGE_FLOW[index + 1];
}

function applicationFromOffer(job: CareerOpportunity): CareerApplication {
  const title = job.title || "Role";
  const company = job.company || "Company";
  const now = Date.now() / 1000;
  return {
    id: `local-${job.id}`,
    opportunity_id: job.id,
    title,
    company,
    source: job.source || "",
    cv_name: job.cv_name,
    cv_text: job.cv_text,
    cv: job.cv,
    cover: job.cover,
    generation: job.generation,
    summary: job.cv?.summary,
    ats_notes: job.ats_notes,
    pack_ready: Boolean(job.pack_ready),
    stage: job.stage,
    apply_mode: "manual",
    next_action: job.next_action,
    created_at: now,
    updated_at: now,
  };
}

function factsCopy(tx: Tx): OfferFactCopy {
  return {
    unknown: tx("factUnknown", "Not listed"),
    remote: tx("factRemote", "Remote"),
    office: tx("factOffice", "On-site days"),
    country: tx("factCountry", "Country"),
    city: tx("factCity", "City"),
    pay: tx("factPay", "Pay"),
    duration: tx("factDuration", "Duration"),
    workRemote: tx("workRemote", "Full remote"),
    workHybrid: tx("workHybrid", "Hybrid"),
    workOnsite: tx("workOnsite", "On site"),
    perDay: tx("factPerDay", "/day"),
    perYear: tx("factPerYear", "/yr"),
    dayRate: tx("factDayRate", "Day rate"),
    salary: tx("factSalary", "Salary"),
    contract: tx("factContract", "Contract"),
    experience: tx("factExperience", "Experience"),
    start: tx("factStart", "Start"),
    startAsap: tx("factStartAsap", "As soon as possible"),
    contracts: {
      contractor: tx("contract_contractor", "Freelance"),
      permanent: tx("contract_permanent", "Permanent"),
      "fixed-term": tx("contract_fixed_term", "Fixed-term"),
      "part-time": tx("contract_part_time", "Part-time"),
      temporary: tx("contract_temporary", "Temporary"),
      internship: tx("contract_internship", "Internship"),
      apprenticeship: tx("contract_apprenticeship", "Apprenticeship"),
    },
    experiences: {
      junior: tx("experience_junior", "Junior (0-2 years)"),
      mid: tx("experience_mid", "Confirmed (3-5 years)"),
      senior: tx("experience_senior", "Senior (6-10 years)"),
      expert: tx("experience_expert", "Expert (10+ years)"),
    },
  };
}

/** Free-Work palette: green freelance, orange permanent, blue fixed-term. */
const BADGE_TONE: Record<string, string> = {
  contractor: "bg-emerald-600",
  permanent: "bg-orange-500",
  "fixed-term": "bg-sky-600",
  "part-time": "bg-violet-600",
  temporary: "bg-amber-600",
  internship: "bg-slate-500",
  apprenticeship: "bg-slate-500",
};

/** Contract pills the way the boards print them (Freelance, CDI, CDD...). */
function OfferBadges({ row, copy }: { row: CareerOpportunity; copy: OfferFactCopy }) {
  const kinds = offerContracts(row);
  if (!kinds.length) return null;
  return (
    <span className="flex flex-wrap items-center gap-1" data-testid="career-offer-badges">
      {kinds.map((kind) => (
        <span
          key={kind}
          className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide text-white", BADGE_TONE[kind] || "bg-slate-500")}
        >
          {contractLabel(kind, copy)}
        </span>
      ))}
    </span>
  );
}

export function CareerWorkspace({
  chatOpen,
  onToggleChat,
  onSeed,
  deskPane,
  jobId,
  onDeskPane,
}: {
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onSeed?: (text: string) => void;
  deskPane?: "home" | "offers";
  jobId?: string;
  onDeskPane?: (pane: "home" | "offers", job?: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const theme = useThemeValue();
  const fluentTheme = useCareerTheme(theme);
  const { token } = useClient();
  const reduceMotion = useReducedMotion();
  const [live, setLive] = useState<CareerDesk | null>(null);
  const [error, setError] = useState("");
  const [apiOffline, setApiOffline] = useState(false);
  const [deskReady, setDeskReady] = useState(false);
  const [linkedinMcpOn, setLinkedinMcpOn] = useState(false);
  const [busy, setBusy] = useState("");
  const [track, setTrack] = useState<CareerTrack>("freelance");
  const [pane, setPane] = useState<Pane>("offers");
  const [offerListView, setOfferListView] = useState<OfferListView>("inbox");
  const [selectedId, setSelectedId] = useState(() => jobId || careerHashJob());
  const [titles, setTitles] = useState("");
  const [primary, setPrimary] = useState("");
  const [secondary, setSecondary] = useState("");
  const [excluded, setExcluded] = useState("");
  const [stack, setStack] = useState("");
  const [minRate, setMinRate] = useState("");
  const [minSalary, setMinSalary] = useState("");
  const [available, setAvailable] = useState("");
  const [languages, setLanguages] = useState("fr, en");
  const [workMode, setWorkMode] = useState("remote");
  const [inboxText, setInboxText] = useState("");
  const [localApps, setLocalApps] = useState<CareerApplication[]>([]);
  const [localRows, setLocalRows] = useState<CareerOpportunity[]>([]);
  const [localInbox, setLocalInbox] = useState<CareerInboxItem[]>([]);
  const [localFollowup, setLocalFollowup] = useState("");
  const [bucketFilter, setBucketFilter] = useState<"all" | "perfect" | "good" | "skip">("all");
  const [copied, setCopied] = useState("");
  const [searchNote, setSearchNote] = useState("");
  const [atsBoards, setAtsBoards] = useState("databricks, stripe, spotify, notion");
  const [view, setView] = useState<CareerShellView>("work");
  const stayOnSetup = useRef(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<"start" | "edit">("start");
  const [mailSettingsOpen, setMailSettingsOpen] = useState(false);
  const [mailOfferId, setMailOfferId] = useState("");
  const [mailPreview, setMailPreview] = useState<CareerMailDraft | null>(null);

  const tx = useCallback(
    (key: string, fallback: string, values?: Record<string, string | number>) =>
      t(`studio.career.${key}`, { defaultValue: fallback, ...values }),
    [t],
  );

  const fallback = useMemo(() => emptyCareerDesk(), []);
  const desk = live
    ? {
        ...live,
        catalog: live.catalog?.length ? live.catalog : fallback.catalog,
        stack: live.stack?.skills?.length ? live.stack : fallback.stack,
      }
    : fallback;

  const loadGen = useRef(0);
  const searchAbortRef = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    const gen = ++loadGen.current;
    try {
      const next = await fetchCareerDesk(token || "");
      if (gen !== loadGen.current) return;
      setLive(next);
      setApiOffline(false);
      setError("");
    } catch {
      if (gen !== loadGen.current) return;
      setApiOffline(true);
    } finally {
      if (gen === loadGen.current) setDeskReady(true);
    }
  }, [token]);

  const refreshDesk = useCallback(async () => {
    setBusy("refresh");
    setError("");
    try {
      const statusDesk = await postCareer(token || "", "status", {});
      setLive(statusDesk);
      setApiOffline(false);
      try {
        const snap = await fetchCareerDesk(token || "");
        setLive(snap);
      } catch {
        /* status already applied */
      }
      loadGen.current += 1;
    } catch (err) {
      setApiOffline(true);
      setError((err as Error).message || tx("loadError", "Could not load Career."));
    } finally {
      setDeskReady(true);
      setBusy((current) => (current === "refresh" ? "" : current));
    }
  }, [token, tx]);

  useEffect(() => {
    if (!token) setDeskReady(true);
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const hunting = desk.loop?.enabled || desk.loop?.phase === "hunt" || desk.loop?.phase === "busy";
    if (!token || !hunting) return undefined;
    const id = window.setInterval(() => {
      void load();
    }, 8_000);
    return () => window.clearInterval(id);
  }, [token, desk.loop?.enabled, desk.loop?.phase, load]);

  useEffect(() => {
    if (!token) return;
    void fetchMcpPresets(token)
      .then((payload) => {
        const row = payload.presets.find((preset) => preset.name === "linkedin");
        setLinkedinMcpOn(Boolean(row?.configured));
      })
      .catch(() => setLinkedinMcpOn(false));
  }, [token]);

  useEffect(() => {
    if (!live?.profile) return;
    const p = live.profile;
    setTitles((current) => joinList(p.titles) || current);
    setPrimary((current) => joinList(p.countries_primary) || current);
    setSecondary((current) => joinList(p.countries_secondary) || current);
    setExcluded((current) => joinList(p.countries_excluded) || current);
    setStack((current) => joinList(p.stack) || current);
    setMinRate((current) => (p.min_rate ? String(p.min_rate) : current));
    setMinSalary((current) => (p.min_salary ? String(p.min_salary) : current));
    setAvailable(String(p.available_from || ""));
    setLanguages(joinList(p.languages));
    setWorkMode(String(p.work_mode || "remote"));
    setAtsBoards((current) => joinList(p.ats_boards) || current);
    if (p.track === "jobs" || p.track === "freelance") setTrack(p.track);
  }, [live?.profile]);

  const run = useCallback(
    async (action: string, body: Record<string, unknown> = {}, signal?: AbortSignal) => {
      setBusy(action);
      setError("");
      try {
        const result = await postCareer(token || "", action, body, signal);
        if (signal?.aborted) return null;
        setLive(result);
        setApiOffline(false);
        return result;
      } catch (err) {
        if (isAbortError(err) || signal?.aborted) return null;
        const status = err instanceof ApiError ? err.status : 0;
        if (!status || status >= 500) setApiOffline(true);
        setError(careerMailError((err as Error).message || tx("actionFailed", "Career action failed."), i18n.language.startsWith("fr")));
        return null;
      } finally {
        if (!signal?.aborted) setBusy("");
      }
    },
    [token, tx, i18n.language],
  );

  const openMail = async (id: string, recipient = "") => {
    setMailOfferId(id);
    setMailPreview(null);
    const result = await run("mail_draft", { id, recipient });
    if (result?.mail_draft) setMailPreview(result.mail_draft);
  };

  const stopSearch = useCallback(() => {
    searchAbortRef.current?.abort();
    searchAbortRef.current = null;
    setBusy("");
    setSearchNote(tx("searchStopped", "Search stopped. Offers already found stay in the list."));
  }, [tx]);

  const patchOfferLocal = useCallback((id: string, patch: Partial<CareerOpportunity> | null) => {
    const apply = (list: CareerOpportunity[]) =>
      patch === null ? list.filter((row) => row.id !== id) : list.map((row) => (row.id === id ? { ...row, ...patch } : row));
    setLocalRows((current) => apply(current));
    setLive((current) =>
      current ? { ...current, opportunities: apply(current.opportunities || []) } : current,
    );
  }, []);

  const handleOfferAction = useCallback(
    async (id: string, action: OfferRowAction) => {
      if (action === "view") {
        setSelectedId(id);
        return;
      }
      const api = action;
      const localPatch: Partial<CareerOpportunity> | null =
        action === "delete"
          ? null
          : action === "favorite"
            ? { favorite: true }
            : action === "unfavorite"
              ? { favorite: false }
              : action === "archive"
                ? { archived: true, archived_at: Date.now() / 1000 }
                : { archived: false, archived_at: null };
      const result = await run(api, { id });
      if (!result) patchOfferLocal(id, localPatch);
      if (action === "delete" && selectedId === id) setSelectedId("");
    },
    [patchOfferLocal, run, selectedId],
  );

  const saveWizard = useCallback(
    async (body: Record<string, unknown>) => {
      setLive((current) => ({
        ...(current || fallback),
        profile: { ...(current?.profile || fallback.profile), ...body },
      }));
      if (body.wizard_complete) {
        stayOnSetup.current = false;
        setView("work");
      }
      void run("profile", body);
      return true;
    },
    [fallback, run],
  );

  const mergedRows = useMemo(() => {
    const byKey = new Map<string, CareerOpportunity>();
    for (const row of [...localRows, ...(desk.opportunities || [])]) {
      if (!row?.id) continue;
      const key = opportunityKey(row);
      byKey.set(key, { ...(byKey.get(key) || {}), ...row });
    }
    return [...byKey.values()];
  }, [desk.opportunities, localRows]);

  const profileForScore = useMemo(
    () => ({
      ...desk.profile,
      track,
      titles: titles.split(","),
      countries_primary: primary.split(","),
      countries_secondary: secondary.split(","),
      countries_excluded: excluded.split(","),
      stack: stack.split(","),
      languages: languages.split(","),
      min_rate: Number(minRate) || Number(desk.profile?.min_rate) || 0,
      min_salary: Number(minSalary) || Number(desk.profile?.min_salary) || 0,
      work_mode: workMode,
      available_from: available,
      ats_boards: atsBoards.split(","),
    }),
    [atsBoards, available, desk.profile, excluded, languages, minRate, minSalary, primary, secondary, stack, titles, track, workMode],
  );

  const scoredRows = useMemo(() => {
    return mergedRows
      .filter((row) => {
        if (isJobListingPage(row.title || "", row.url || "")) return false;
        const stage = row.stage || "discovered";
        if (["applied", "replied", "interview", "offer", "won"].includes(stage)) {
          /* keep in-flight offers even if the title is a weak match */
        } else if (!jobIsRelevant(row, profileForScore)) {
          return false;
        }
        if (row.source === "remotive" || row.remote === "remote") return true;
        if (track === "freelance") return (row.track || "freelance") !== "jobs";
        return (row.track || "jobs") !== "freelance";
      })
      .map((row) => scoreOpportunity(row, profileForScore))
      .sort((a, b) => Number(b.match_score || 0) - Number(a.match_score || 0));
  }, [mergedRows, profileForScore, track]);

  const activeScored = useMemo(() => activeOffers(scoredRows), [scoredRows]);
  const rows = useMemo(() => {
    return activeScored.filter((row) => bucketFilter === "all" || row.bucket === bucketFilter);
  }, [activeScored, bucketFilter]);

  const selected = selectedId
    ? rows.find((row) => row.id === selectedId) ||
      scoredRows.find((row) => row.id === selectedId) ||
      mergedRows.find((row) => row.id === selectedId) ||
      null
    : null;

  useEffect(() => {
    const job = (jobId || "").trim();
    if (job) {
      setSelectedId(job);
      setPane("offers");
      if (!stayOnSetup.current) setView("work");
      return;
    }
    setPane("offers");
    if (deskPane === "offers" && !stayOnSetup.current) setView("work");
  }, [deskPane, jobId]);

  const apps = useMemo(() => {
    const remote = desk.applications || [];
    const extra = localApps.filter((app) => !remote.some((item) => item.opportunity_id === app.opportunity_id));
    return dedupeApplications([...extra, ...remote]);
  }, [desk.applications, localApps]);
  const inbox = useMemo(() => {
    const remote = desk.inbox || [];
    const extra = localInbox.filter((item) => !remote.some((row) => row.id === item.id));
    return [...extra, ...remote];
  }, [desk.inbox, localInbox]);
  const followupBody = localFollowup || desk.followup?.body || "";

  /** Same gate as Tenders: configuration stepper first, desk after wizard_complete. */
  const wizardComplete = Boolean(desk.profile?.wizard_complete);
  const hasOffers = activeScored.length > 0;
  const showWizard = careerShowsWizard({ deskReady, wizardComplete, view, hasOffers });
  const showDesk = careerShowsDesk({ deskReady, wizardComplete, view, hasOffers });
  const canLeaveSetup = wizardComplete || hasOffers;

  useEffect(() => {
    if (deskReady && !wizardComplete && view === "work" && !(jobId || "").trim() && !hasOffers) {
      setView("setup");
    }
  }, [deskReady, hasOffers, jobId, view, wizardComplete]);

  const panes = track === "freelance" ? FREELANCE_PANES : JOBS_PANES;
  // The brief now comes from the profile titles (the dashboard prompt box is gone).
  const query = titles;
  const marketIsos = expandSearchCountries(
    [...primary.split(","), ...secondary.split(",")],
    excluded.split(","),
  );
  const portals = authorizedPortals(query, marketIsos.length ? marketIsos : [...DEFAULT_SEARCH_COUNTRIES], track, workMode);
  const lang = (i18n.language || "fr").slice(0, 2);
  const domainHints = useMemo(
    () =>
      [...(profileForScore.stack || []), ...(profileForScore.titles || [])]
        .map((item) => String(item).trim())
        .filter(Boolean),
    [profileForScore.stack, profileForScore.titles],
  );
  const mergeRows = (found: CareerOpportunity[]) => {
    setLocalRows((current) => {
      const byKey = new Map(current.map((row) => [opportunityKey(row), row]));
      for (const row of found) {
        const key = opportunityKey(row);
        const next = {
          ...(byKey.get(key) || {}),
          ...row,
          description: stripHtml(row.description || (byKey.get(key)?.description || "")),
        };
        byKey.set(key, next);
      }
      return [...byKey.values()];
    });
    if (found[0]) setSelectedId(found[0].id);
  };

  const searchLive = async (text: string, mode: "search" | "find" = "search") => {
    const query = text.trim() || titles;
    const searchTitle = query.split(",")[0].trim() || query;
    const searchProfile = {
      ...profileForScore,
      titles: [searchTitle, ...String(titles || "").split(",")].map((item) => item.trim()).filter(Boolean),
    };
    const searchIsos = marketIsos.length ? marketIsos : [...DEFAULT_SEARCH_COUNTRIES];
    searchAbortRef.current?.abort();
    const ctrl = new AbortController();
    searchAbortRef.current = ctrl;
    const { signal } = ctrl;
    setBusy(mode === "find" ? "find" : "search");
    setError("");
    setSearchNote("");
    try {
      const [remotive, ats, web] = await Promise.all([
        fetchRemotiveOpportunities(searchTitle, searchProfile, track, signal).catch((err) => {
          if (isAbortError(err) || signal.aborted) throw err;
          return [] as CareerOpportunity[];
        }),
        fetchOfficialAtsBoards(atsBoards.split(","), searchProfile, track, signal).catch(() => [] as CareerOpportunity[]),
        fetchWebSearchOpportunities(
          searchTitle,
          searchIsos,
          searchProfile,
          track,
          stack.split(","),
          signal,
        ).catch((err) => {
          if (isAbortError(err) || signal.aborted) throw err;
          return [] as CareerOpportunity[];
        }),
      ]);
      if (signal.aborted) return;
      const found = [...remotive, ...ats, ...web].filter((row) => {
        if (isJobListingPage(row.title || "", row.url || "")) return false;
        return jobIsRelevant(row, searchProfile);
      });
      mergeRows(found);
      if (found.length) await run("ingest", { jobs: found.slice(0, 40).map(compactHit), track }, signal);
      if (signal.aborted) return;
      const remote = await run(
        mode === "find" ? "find" : "search",
        mode === "find"
          ? { brief: query, track, countries: searchIsos }
          : { query, track, countries: searchIsos },
        signal,
      );
      if (signal.aborted) return;
      const remoteCount = remote?.opportunities?.length || 0;
      if (!found.length && !remoteCount) {
        setSearchNote(
          tx(
            "searchEmpty",
            "No live hits yet. Open LinkedIn or France Travail, or paste an offer below.",
          ),
        );
      } else {
        setSearchNote(
          tx("searchCounts", "{{remotive}} Remotive · {{ats}} ATS · {{web}} web", {
            remotive: remotive.length,
            ats: ats.length,
            web: web.length,
          }),
        );
      }
    } catch (err) {
      if (isAbortError(err) || signal.aborted) {
        setSearchNote(tx("searchStopped", "Search stopped. Offers already found stay in the list."));
        return;
      }
      setSearchNote((err as Error).message || tx("actionFailed", "Career action failed."));
    } finally {
      if (searchAbortRef.current === ctrl) {
        searchAbortRef.current = null;
        setBusy("");
      }
    }
  };

  const importPasted = (raw: { url: string; title: string; company: string; body: string }) => {
    const parsed = parsePastedOffer({
      ...raw,
      track,
      country: primary.split(",")[0] || "",
    });
    if (!parsed) {
      setError(tx("importNeed", "Paste a title or the official URL, plus the description you copied."));
      return;
    }
    const scored = scoreOpportunity(parsed, profileForScore);
    mergeRows([scored]);
    setSearchNote(tx("importOk", "Offer imported from your paste. LinkedIn and closed boards were not fetched."));
    openOffers({ view: "inbox", bucket: "all", id: scored.id });
    void run("import", {
      url: parsed.url,
      title: parsed.title,
      company: parsed.company,
      body: parsed.description,
      track,
      country: parsed.country,
    });
  };

  const classifyLocal = () => {
    const body = inboxText.trim();
    if (!body) return;
    const classification = classifyReply(body);
    const item: CareerInboxItem = {
      id: `in-local-${Date.now()}`,
      opportunity_id: selected?.id || "",
      sender: "Recruiter",
      subject: body.slice(0, 80),
      body,
      classification,
    };
    setLocalInbox((current) => [item, ...current]);
    if (selected && classification === "interview") {
      setLocalRows((current) =>
        current.map((row) => (row.id === selected.id ? { ...row, stage: "interview" } : row)),
      );
    }
    void run("inbox", { body, opportunity_id: selected?.id || "", subject: body.slice(0, 80) });
  };

  const followTarget = selected
    || rows.find((row) => row.stage === "applied" || row.stage === "ready" || row.stage === "replied")
    || rows[0];

  const followLocal = (wave: "j3" | "j7") => {
    if (!followTarget) return;
    setLocalFollowup(draftFollowup(followTarget, wave));
    void run("followup", { id: followTarget.id, wave });
  };

  const jobById = useCallback(
    (id: string) =>
      rows.find((row) => row.id === id) ||
      scoredRows.find((row) => row.id === id) ||
      mergedRows.find((row) => row.id === id) ||
      null,
    [mergedRows, rows, scoredRows],
  );

  const rememberApp = useCallback(
    (job: CareerOpportunity, extra: Partial<CareerApplication> = {}) => {
      const now = Date.now() / 1000;
      setLocalApps((current) => {
        const pack = current.find((item) => item.opportunity_id === job.id) || applicationFromOffer(job);
        return dedupeApplications([
          {
            ...pack,
            title: job.title || pack.title,
            company: job.company || pack.company,
            source: job.source || pack.source,
            created_at: pack.created_at || now,
            updated_at: now,
            ...extra,
          },
          ...current.filter((item) => item.opportunity_id !== job.id),
        ]);
      });
    },
    [],
  );

  const prepareOffer = async (id?: string) => {
    const job = (id && jobById(id)) || selected;
    if (!job) return;
    const result = await run("prepare", { id: job.id });
    if (result?.prepared) {
      rememberApp(job, result.prepared);
      return result.prepared;
    }
  };

  const applyOffer = async (id?: string) => {
    const job = (id && jobById(id)) || selected;
    if (!job) return;
    const oid = job.id;
    const url = job.url;
    if (!job.pack_ready && !job.cv_text) {
      await prepareOffer(oid);
    }
    if (url) {
      openOfficialCareerUrl(token || "", url);
    } else {
      setError(tx("applyNoUrl", "This offer has no official URL. Paste the employer page first."));
    }
    const openedNext = tx("appsOpenedNext", "Opened the employer page. Submit the tailored CV there.");
    const result = await run("apply", { id: oid });
    rememberApp(job, {
      employer_opened: true,
      stage: "ready",
      next_action: openedNext,
    });
    if (!result) {
      patchOfferLocal(oid, { employer_opened: true, next_action: openedNext, stage: "ready" });
    }
  };

  const markApplied = async (id: string) => {
    const job = jobById(id);
    const now = Date.now() / 1000;
    const next = tx("appsMarkedNext", "Marked as sent. Follow up if the board stays quiet.");
    const result = await run("stage", { id, stage: "applied" });
    if (job) {
      rememberApp(job, { stage: "applied", applied_at: now, next_action: next });
    } else {
      setLocalApps((current) =>
        current.map((item) =>
          item.opportunity_id === id
            ? { ...item, stage: "applied", applied_at: now, updated_at: now, next_action: next }
            : item,
        ),
      );
    }
    if (!result) patchOfferLocal(id, { stage: "applied", applied_at: now, next_action: next });
  };

  const prepareSelected = () => prepareOffer();
  const applySelected = () => applyOffer();

  const downloadSelected = async (id: string, kind: "cv_docx" | "cover_docx" = "cv_docx") => {
    const row = rows.find((item) => item.id === id) || selected;
    if (row && !row.pack_ready && !row.cv_text) {
      if (!await prepareOffer(id)) return;
    }
    setBusy("download");
    setError("");
    try {
      if (token) {
        const result = await postCareer(token, "download", { id, kind });
        if (result.download?.data) {
          triggerCareerDownload(result.download);
          setLive(result);
          return;
        }
      }
      setError(tx("downloadCvFailed", "Download failed. Tailor the CV to this mission first."));
    } catch (err) {
      setError((err as Error).message || tx("downloadCvFailed", "Download failed. Tailor the CV to this mission first."));
    } finally {
      setBusy("");
    }
  };

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text.slice(0, 24));
    } catch {
      setCopied("");
    }
  };

  const badgeFor = (id: string) => {
    if (id === "offers") return activeScored.length;
    if (id === "applications") return apps.length;
    if (id === "inbox") return inbox.length;
    if (id === "pipeline") return rows.length;
    if (id === "interviews") return rows.filter((row) => row.stage === "interview").length;
    return 0;
  };

  const goDesk = (job?: string) => {
    stayOnSetup.current = false;
    setView("work");
    setPane("offers");
    setSelectedId((job || "").trim());
    if (onDeskPane) onDeskPane("offers", job);
    else replaceCareerHash(careerDeskHash({ pane: "offers", job }));
  };

  const selectOffer = (id: string) => {
    const next = id.trim();
    setSelectedId(next);
    if (onDeskPane) onDeskPane("offers", next || undefined);
    else replaceCareerHash(careerDeskHash({ pane: "offers", job: next || undefined }));
  };

  const openOffers = (next?: OfferDeskOpen) => {
    if (next?.view) setOfferListView(next.view);
    if (next?.view === "favorites" || next?.view === "archive") {
      setBucketFilter(next.bucket || "all");
    } else if (next?.bucket) {
      setBucketFilter(next.bucket);
    }
    goDesk(next?.id);
  };

  const openSetup = () => {
    stayOnSetup.current = true;
    setView("setup");
  };

  const submitSchedule = async (schedule: CareerLoopSchedule, runNow: boolean) => {
    const tz = browserTimeZone();
    const action = scheduleMode === "start" ? "start" : "schedule";
    const result = await run(action, {
      schedule: { ...schedule, tz: tz || schedule.tz || null },
      tz,
      run_now: runNow,
    });
    if (result) setScheduleOpen(false);
  };

  return (
    <Customizer settings={{ theme: fluentTheme }}>
      <div
        className={cn(
          "flex h-full min-h-0 min-w-0 w-full flex-1 flex-col overflow-hidden bg-background",
          chatOpen ? "pr-0" : "",
        )}
        style={{ paddingRight: NOTIFICATION_GUTTER, WebkitFontSmoothing: "antialiased" }}
      >
        <header className="flex shrink-0 flex-nowrap items-center gap-x-3 px-5 py-2.5 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.06)]">
          <div className="flex min-w-0 flex-1 flex-nowrap items-center gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <h1
              className="shrink-0 whitespace-nowrap text-xl font-semibold tracking-tight"
              data-testid="career-title"
            >
              {tx("title", "Navin Career")}
            </h1>
            {!showWizard && desk.profile?.display_name ? (
              <p className="max-w-[10rem] shrink-0 truncate text-sm font-medium text-muted-foreground">
                {desk.profile.display_name}
              </p>
            ) : null}
            <div
              className="flex shrink-0 items-center gap-1.5"
              role="tablist"
              aria-label={tx("tracksAria", "Career tracks")}
            >
              {(["freelance", "jobs"] as const).map((id) => (
                <motion.button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={track === id}
                  whileTap={reduceMotion ? undefined : { scale: 0.96 }}
                  className={cn(
                    "min-h-9 cursor-pointer rounded-full px-3.5 text-sm font-medium outline outline-1 transition-[background-color,color] duration-150",
                    track === id
                      ? "bg-indigo-600 text-white outline-indigo-600"
                      : "bg-background text-foreground outline-black/10 hover:bg-muted/50 dark:outline-white/10",
                  )}
                  onClick={() => {
                    setTrack(id);
                    if (pane === "pipeline" || pane === "interviews") {
                      setPane(id === "freelance" ? "pipeline" : "interviews");
                    }
                    void run("profile", {
                      track: id,
                      engagement: id === "freelance" ? "freelance" : "permanent",
                    });
                  }}
                >
                  {id === "freelance" ? tx("trackFreelance", "Freelance") : tx("trackJobs", "Jobs")}
                </motion.button>
              ))}
            </div>
            {showDesk || (showWizard && canLeaveSetup) ? (
              pane === "offers" && view === "work" ? (
                <PrimaryButton
                  text={tx("offerBook", "Offers")}
                  title={tx("offerBook", "Offers")}
                  iconProps={{ iconName: "PageList" }}
                  onClick={() => openOffers()}
                  styles={HEADER_BUTTON_STYLES}
                  data-testid="career-open-offers"
                />
              ) : (
                <DefaultButton
                  text={tx("offerBook", "Offers")}
                  title={tx("offerBook", "Offers")}
                  iconProps={{ iconName: "PageList" }}
                  onClick={() => openOffers()}
                  styles={HEADER_BUTTON_STYLES}
                  data-testid="career-open-offers"
                />
              )
            ) : null}
            {showDesk ? (
              desk.loop?.enabled ? (
                <PrimaryButton
                  text={tx("pause", "Pause")}
                  title={tx("pauseTitle", "Pause the loop")}
                  ariaLabel={tx("pauseTitle", "Pause the loop")}
                  iconProps={{ iconName: "Pause" }}
                  onClick={() => void run("stop")}
                  styles={HEADER_BUTTON_STYLES}
                  data-testid="career-pause-loop"
                />
              ) : (
                <PrimaryButton
                  text={tx("startLoop", "Start")}
                  title={tx("startLoopTitle", "Start the loop")}
                  ariaLabel={tx("startLoopTitle", "Start the loop")}
                  iconProps={{ iconName: "Play" }}
                  onClick={() => {
                    setScheduleMode("start");
                    setScheduleOpen(true);
                  }}
                  disabled={Boolean(busy)}
                  styles={HEADER_BUTTON_STYLES}
                  data-testid="career-start-loop"
                />
              )
            ) : null}
            {showDesk ? (
              <DefaultButton
                text={tx("cycle", "Run")}
                title={tx("cycleTitle", "Run a cycle")}
                ariaLabel={tx("cycleTitle", "Run a cycle")}
                iconProps={{ iconName: "Sync" }}
                onClick={() => void run("tick", { force: true })}
                disabled={Boolean(busy)}
                styles={HEADER_BUTTON_STYLES}
              />
            ) : null}
            {showDesk ? (
              <PrimaryButton
                text={tx("findMission", "Find")}
                title={tx("findMissionTitle", "Find me a mission")}
                ariaLabel={tx("findMissionTitle", "Find me a mission")}
                iconProps={{ iconName: "Search" }}
                disabled={Boolean(busy)}
                onClick={() => {
                  const text = `${titles} ${track} ${primary} ${workMode} ${minRate ? `${minRate}/day` : ""} ${stack}`;
                  void searchLive(text, "find");
                  onSeed?.(
                    `/career Find missions for: ${text}. Call career action=status first (full local book). Then career action=find with this brief. Ingest LinkedIn MCP search_jobs hits (never scrape). Do not invent offers or experience.\n\n`,
                  );
                  openOffers({ view: "inbox", bucket: "all" });
                }}
                styles={HEADER_BUTTON_STYLES}
              />
            ) : null}
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {onToggleChat ? (
              <IconButton
                ariaLabel={chatOpen ? tx("hideChat", "Hide chat") : tx("chat", "Chat")}
                title={chatOpen ? tx("hideChat", "Hide chat") : tx("chat", "Chat")}
                iconProps={{ iconName: chatOpen ? "ChatSolid" : "Chat" }}
                onClick={onToggleChat}
                aria-pressed={Boolean(chatOpen)}
                checked={Boolean(chatOpen)}
                styles={ICON_BUTTON_STYLES}
                data-testid="career-toggle-chat"
              />
            ) : null}
            <IconButton
              ariaLabel={tx("settings", "Settings")}
              title={tx("profileConfig", "Profile setup")}
              iconProps={{ iconName: "Settings" }}
              onClick={() => {
                if (showWizard && canLeaveSetup) openOffers();
                else openSetup();
              }}
              checked={showWizard}
              styles={ICON_BUTTON_STYLES}
              data-testid="career-open-setup"
            />
            <IconButton
              ariaLabel={tx("refresh", "Refresh")}
              title={tx("refresh", "Refresh")}
              iconProps={{ iconName: "Refresh" }}
              disabled={Boolean(busy)}
              onClick={() => void refreshDesk()}
              styles={ICON_BUTTON_STYLES}
              data-testid="career-refresh"
            />
          </div>
        </header>

        {showDesk ? (
        <nav
          className="shrink-0 overflow-x-auto px-5 shadow-[0_1px_0_rgba(15,23,42,0.06)] dark:shadow-[0_1px_0_rgba(255,255,255,0.08)]"
          aria-label={tx("panesAria", "Career panes")}
        >
          <div className="mx-auto flex w-full min-w-max max-w-6xl items-stretch justify-end gap-0.5 py-1">
            {panes.map((item) => {
              const selectedPane = pane === item.id;
              const badge = badgeFor(item.id);
              return (
                <motion.button
                  key={item.id}
                  type="button"
                  whileTap={reduceMotion ? undefined : { scale: 0.96 }}
                  onClick={() => {
                    setPane(item.id);
                  }}
                  data-testid={`career-pane-${item.id}`}
                  className={cn(
                    "relative inline-flex min-h-14 min-w-[5.2rem] shrink-0 cursor-pointer flex-col items-center justify-center gap-1 rounded-xl px-2.5 py-1.5 text-center transition-[background-color,color,box-shadow] duration-150",
                    selectedPane
                      ? "bg-indigo-500/16 font-medium text-foreground shadow-[0_6px_16px_rgba(15,23,42,0.08)]"
                      : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                  )}
                >
                  <Icon iconName={item.icon} className="text-[18px] text-indigo-600 dark:text-indigo-300" aria-hidden />
                  <span className="text-[11px] leading-none tracking-wide">
                    {tx(`pane.${item.id}`, item.id)}
                  </span>
                  {badge ? (
                    <span className="absolute right-1 top-1 rounded-full bg-indigo-500/25 px-1 text-[10px] tabular-nums leading-4">
                      {badge}
                    </span>
                  ) : null}
                </motion.button>
              );
            })}
          </div>
        </nav>
        ) : null}

        <div
          data-testid="career-scroll"
          className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain px-5 py-4 scrollbar-thin scrollbar-track-transparent"
        >
          {error ? (
            <div className="mb-4">
              <MessageBar messageBarType={MessageBarType.warning} onDismiss={() => setError("")}>
                {error}
              </MessageBar>
            </div>
          ) : null}
          {apiOffline ? (
            <p className="mb-4 text-pretty text-sm text-muted-foreground">
              {tx(
                "offlineQuiet",
                "Search still works here. The Career server is offline, so profile and inbox stay on this session.",
              )}
            </p>
          ) : null}
          {busy ? (
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
              <ProgressIndicator
                className="min-w-0 flex-1"
                label={`${tx("working", "Working")} - ${busy}`}
              />
              <DefaultButton
                text={tx("stopSearch", "Stop search")}
                iconProps={{ iconName: "Cancel" }}
                onClick={stopSearch}
                styles={BUTTON_STYLES}
                data-testid="career-stop-search"
              />
            </div>
          ) : null}
          {!deskReady && token ? <DeskSkeleton /> : null}

          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={showWizard ? "setup" : `${track}-${pane}`}
              initial={reduceMotion ? false : { opacity: 0, y: 10, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={reduceMotion ? undefined : { opacity: 0, y: -8, filter: "blur(4px)" }}
              transition={SPRING}
              className="mx-auto flex w-full max-w-6xl flex-col gap-8"
            >
              {showWizard ? (
                <CareerWizard
                  desk={desk}
                  track={track}
                  tx={tx}
                  busy={Boolean(busy)}
                  catalog={desk.catalog || []}
                  token={token || ""}
                  onSave={saveWizard}
                  onSeed={onSeed}
                  onSecret={async (name, value) => Boolean(await run("secret", { name, value }))}
                  onLeave={canLeaveSetup ? () => openOffers() : undefined}
                />
              ) : null}
              {showDesk && pane === "offers" ? (
                <OffersPane
                  tx={tx}
                  rows={rows}
                  allRows={scoredRows}
                  domainHints={domainHints}
                  selected={selected}
                  onSelect={selectOffer}
                  onOfferAction={(id, action) => void handleOfferAction(id, action)}
                  archiveAfterDays={
                    retentionDays({
                      archive_after_days: desk.profile.archive_after_days ?? desk.retention?.archive_after_days,
                      delete_after_days: desk.profile.delete_after_days ?? desk.retention?.delete_after_days,
                    }).archive_after_days
                  }
                  deleteAfterDays={
                    retentionDays({
                      archive_after_days: desk.profile.archive_after_days ?? desk.retention?.archive_after_days,
                      delete_after_days: desk.profile.delete_after_days ?? desk.retention?.delete_after_days,
                    }).delete_after_days
                  }
                  searchNote={searchNote || desk.search?.note}
                  token={token || ""}
                  busy={Boolean(busy)}
                  bucket={bucketFilter}
                  onBucket={setBucketFilter}
                  listView={offerListView}
                  onListView={setOfferListView}
                  onSearch={() => void searchLive(titles)}
                  onPrepare={() => void prepareSelected()}
                  onApply={() => void applySelected()}
                  onEmail={() => selected && void openMail(selected.id)}
                  onMarkApplied={() => selected && void markApplied(selected.id)}
                  onDownload={(id, kind) => void downloadSelected(id, kind)}
                  onCopy={copyText}
                  copied={copied}
                  locale={i18n.language}
                  displayName={desk.profile.display_name}
                  track={track}
                />
              ) : null}
              {showDesk && pane === "applications" ? (
                <ApplicationsPane
                  tx={tx}
                  desk={desk}
                  apps={apps}
                  jobs={[...scoredRows, ...mergedRows]}
                  track={track}
                  pipelineCount={activeScored.length}
                  locale={i18n.language}
                  onPrepare={(id) => void prepareOffer(id)}
                  token={token || ""}
                  onDownload={(id, kind) => void downloadSelected(id, kind)}
                  onApply={(id) => void applyOffer(id)}
                  onEmail={(id) => void openMail(id)}
                  onMarkApplied={(id) => void markApplied(id)}
                  onOpenDiscover={() => openOffers({ view: "inbox", bucket: "all" })}
                  onCopy={copyText}
                />
              ) : null}
              {showDesk && pane === "inbox" ? (
                <>
                <CareerMailAccountSummary
                  profile={desk.profile}
                  status={desk.mailbox_status}
                  french={lang === "fr"}
                  busy={Boolean(busy)}
                  onSettings={() => { setError(""); setMailSettingsOpen(true); }}
                  onSync={() => void run("sync_mail")}
                />
                <InboxPane
                  tx={tx}
                  items={inbox}
                  followup={followupBody}
                  inboxText={inboxText}
                  setInboxText={setInboxText}
                  selectedId={selected?.id || ""}
                  canFollow={Boolean(followTarget)}
                  followLabel={
                    followTarget
                      ? [followTarget.title, followTarget.company].filter(Boolean).join(" · ")
                      : ""
                  }
                  onClassify={classifyLocal}
                  onFollowup={followLocal}
                  onCopy={copyText}
                />
                </>
              ) : null}
              {showDesk && (pane === "pipeline" || pane === "interviews") ? (
                <PipelinePane
                  tx={tx}
                  rows={pane === "interviews" ? rows.filter((row) => row.stage === "interview") : rows}
                  interviewsOnly={pane === "interviews"}
                  onStage={(id, stage) => {
                    setLocalRows((current) =>
                      current.map((row) => (row.id === id ? { ...row, stage } : row)),
                    );
                    void run("stage", { id, stage });
                  }}
                  onOpenDiscover={() => openOffers({ view: "inbox", bucket: "all" })}
                  onOpen={(id) => {
                    openOffers({ view: "inbox", bucket: "all", id });
                  }}
                />
              ) : null}
              {showDesk && pane === "profile" ? (
                <>
                <CareerMailAccountSummary
                  profile={desk.profile}
                  status={desk.mailbox_status}
                  french={lang === "fr"}
                  busy={Boolean(busy)}
                  onSettings={() => { setError(""); setMailSettingsOpen(true); }}
                  onSync={() => void run("sync_mail")}
                />
                <ProfilePane
                  tx={tx}
                  track={track}
                  profile={desk.profile}
                  files={desk.files}
                  onReopen={openSetup}
                  busy={Boolean(busy)}
                  onAiAssist={(enabled) => void run("profile", { ai_assist: enabled })}
                />
                <CareerSourcesBlock
                  tx={tx}
                  catalog={desk.catalog || []}
                  mcp={desk.stack?.mcp || []}
                  linkedinMcpOn={linkedinMcpOn}
                  portals={portals}
                  token={token || ""}
                  onImport={importPasted}
                  employers={desk.employers}
                  userEmployers={desk.profile?.employers || []}
                  hiddenEmployers={desk.profile?.employers_hidden || []}
                  busy={Boolean(busy)}
                  onSaveProfile={(patch) => void saveWizard(patch)}
                />
                </>
              ) : null}
            </motion.div>
          </AnimatePresence>
        </div>
        <TradingLoopSchedulePanel
          key={`${scheduleMode}-${scheduleOpen ? "open" : "shut"}`}
          open={scheduleOpen}
          mode={scheduleMode}
          schedule={desk.loop?.schedule}
          nextDue={desk.loop?.next_due}
          enabled={Boolean(desk.loop?.enabled)}
          locale={i18n.language}
          busy={Boolean(busy)}
          tx={tx}
          onDismiss={() => setScheduleOpen(false)}
          onSubmit={(schedule, runNow) => void submitSchedule(schedule, runNow)}
        />
        <CareerMailSettings
          key={mailSettingsOpen ? "mail-settings-open" : "mail-settings-closed"}
          open={mailSettingsOpen}
          profile={desk.profile}
          status={desk.mailbox_status}
          french={lang === "fr"}
          busy={Boolean(busy)}
          error={error}
          loopEnabled={Boolean(desk.loop?.enabled)}
          onDismiss={() => setMailSettingsOpen(false)}
          onSchedule={() => { setMailSettingsOpen(false); setScheduleMode("start"); setScheduleOpen(true); }}
          run={run}
        />
        <CareerMailCompose
          open={Boolean(mailOfferId)}
          draft={mailPreview}
          receipt={desk.mailbox_status?.receipts?.find((row) => row.opportunity_id === mailOfferId)}
          french={lang === "fr"}
          busy={Boolean(busy)}
          error={error}
          accountEnabled={desk.profile.mailbox?.enabled === true}
          onDismiss={() => { setMailOfferId(""); setMailPreview(null); }}
          onSettings={() => { setMailOfferId(""); setMailSettingsOpen(true); setError(""); }}
          onRefresh={(recipient) => void openMail(mailOfferId, recipient)}
          onSend={(reviewed, retry) => mailPreview && void run("send_email", {
            id: mailOfferId, recipient: mailPreview.recipient, revision: mailPreview.revision, reviewed, retry,
          })}
          onDownload={(kind) => void downloadSelected(mailOfferId, kind)}
        />
      </div>
    </Customizer>
  );
}

function Surface({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn(SURFACE, className)}>{children}</div>;
}

function OfferFactsRow({ facts }: { facts: OfferFact[] }) {
  return (
    <dl className="mt-2 flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-xs" data-testid="career-offer-facts">
      {facts.map((fact) => (
        <div key={fact.id} className="flex min-w-0 max-w-full items-baseline gap-1">
          <dt className="shrink-0 text-muted-foreground">{fact.label}</dt>
          <dd className="truncate font-medium" title={fact.value}>
            {fact.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function OfferDetail({
  tx,
  row,
  copy,
  locale,
  track,
  token,
  busy,
  copied,
  displayName,
  onPrepare,
  onApply,
  onEmail,
  onMarkApplied,
  onDownload,
  onCopy,
}: {
  tx: Tx;
  row: CareerOpportunity;
  copy: OfferFactCopy;
  locale: string;
  track: CareerTrack;
  token: string;
  busy: boolean;
  copied: string;
  displayName?: string;
  onPrepare: () => void;
  onApply: () => void;
  onEmail: () => void;
  onMarkApplied: () => void;
  onDownload: (id: string, kind?: "cv_docx" | "cover_docx") => void;
  onCopy: (text: string) => void;
}) {
  const description = stripHtml(row.description || "");
  return (
    <div className="grid gap-4" data-testid="career-offer-panel">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-pretty text-sm text-muted-foreground">
          {[row.company, row.source].filter(Boolean).join(" · ")}
        </p>
        <OfferBadges row={row} copy={copy} />
      </div>
      <OfferFactsRow facts={offerFacts(row, copy, locale, track)} />
      <p className="max-h-64 overflow-auto whitespace-pre-wrap text-pretty text-sm leading-relaxed">
        {description || tx("noDescription", "No description stored. Open the original URL.")}
      </p>
      <p className="text-pretty text-xs text-muted-foreground">
        {tx(
          "applyOpens",
          "Prepare tailors your CV to this mission. Apply then opens the official page. Navin never applies for you.",
        )}
      </p>
      <div className="flex flex-wrap gap-3">
        <PrimaryButton
          styles={BUTTON_STYLES}
          text={tx("prepare", "Tailor CV to this mission")}
          data-testid="career-prepare-pack"
          disabled={busy}
          onClick={onPrepare}
        />
        <DefaultButton
          styles={BUTTON_STYLES}
          text={tx("apply", "Apply on the site")}
          disabled={Boolean(busy)}
          data-testid="career-apply-offer"
          onClick={onApply}
        />
        <DefaultButton
          styles={BUTTON_STYLES}
          text={locale.startsWith("fr") ? "Candidature par email" : "Apply by email"}
          iconProps={{ iconName: "Mail" }}
          disabled={busy || !row.pack_ready}
          onClick={onEmail}
          data-testid="career-offer-email"
        />
        <DefaultButton
          styles={BUTTON_STYLES}
          text={tx("markApplied", "Mark as sent")}
          disabled={Boolean(busy) || row.stage === "applied" || row.stage === "won"}
          data-testid="career-mark-applied-offer"
          onClick={onMarkApplied}
        />
        <DefaultButton
          styles={BUTTON_STYLES}
          text={tx("downloadCv", "Download tailored CV")}
          data-testid="career-download-cv"
          disabled={busy}
          onClick={() => onDownload(row.id)}
        />
        {row.url ? (
          <DefaultButton
            styles={BUTTON_STYLES}
            text={tx("openSource", "Open original")}
            onClick={() => openOfficialCareerUrl(token, row.url || "")}
          />
        ) : null}
        {row.cover ? (
          <>
            <DefaultButton
              styles={BUTTON_STYLES}
              text={tx("downloadCover", "Download cover letter")}
              disabled={busy}
              onClick={() => onDownload(row.id, "cover_docx")}
            />
            <DefaultButton
              styles={BUTTON_STYLES}
              text={copied ? tx("copied", "Copied") : tx("copyCover", "Copy message")}
              onClick={() => onCopy(row.cover || "")}
            />
          </>
        ) : null}
      </div>
      {row.cv_name ? (
        <p className="text-sm">
          {tx("cvReady", "Tailored CV")}: {row.cv_name}
        </p>
      ) : null}
      {row.ats_notes ? (
        <p className="text-pretty text-sm text-muted-foreground">{row.ats_notes}</p>
      ) : null}
      <DocumentGenerationNotice generation={row.generation} />
      {row.cv ? (
        <div data-testid="career-cv-text">
          <CvPreview
            name={row.cv.name || displayName}
            headline={row.cv.headline}
            target={row.cv.target}
            contacts={row.cv.contacts}
            summary={row.cv.summary}
            skills={row.cv.skills}
            strengths={row.cv.strengths}
            highlights={row.cv.highlights}
            sections={row.cv.sections}
            experiences={row.cv.experiences}
            education={row.cv.education}
            languages={row.cv.languages}
            cover={row.cover}
            labels={cvLabels(tx, row.cv.language)}
          />
        </div>
      ) : row.cv_text ? (
        <div data-testid="career-cv-text">
          <DossierPreview title={tx("cvReady", "Tailored CV")} body={row.cv_text} />
        </div>
      ) : null}
    </div>
  );
}

function applicationStatusLabel(view: ApplicationView, tx: Tx): string {
  const key = applicationStatusKey(view);
  if (key === "pack") return tx("appsStagePack", "Pack ready");
  if (key === "opened") return tx("appsStageOpened", "Employer page opened");
  return tx(`stage.${view.stage}`, view.stage);
}

function CareerMcpOptions({
  tx,
  rows,
  linkedinMcpOn,
}: {
  tx: Tx;
  rows: CareerMcpHint[];
  linkedinMcpOn?: boolean;
}) {
  const linkedin = rows.find((row) => (row.id || "").toLowerCase() === "linkedin");
  const others = rows.filter((row) => row !== linkedin);
  if (!rows.length) return null;
  return (
    <div className="space-y-3" data-testid="career-mcp-options">
      <div>
        <p className="text-sm font-semibold">{tx("mcpTitle", "LinkedIn MCP (recommended)")}</p>
        <p className="mt-1 max-w-xl text-pretty text-sm text-muted-foreground">
          {tx(
            "mcpBody",
            "Best option after you enable it: jobs, saved jobs, profile, people and inbox through your LinkedIn session. Never scrape. Navin never applies for you. Connection requests and messages need your confirmation.",
          )}
        </p>
        <div className="mt-3">
          <DefaultButton
            text={tx("openCareerMcp", "Open Settings > Tools")}
            iconProps={{ iconName: "Settings" }}
            onClick={() => openToolsHash()}
            styles={BUTTON_STYLES}
          />
        </div>
      </div>
      {linkedin ? (
        <div className="grid gap-2" data-testid="career-linkedin-mcp">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{linkedin.name || "LinkedIn"}</span>
            {linkedin.recommended ? (
              <span className="rounded-full bg-indigo-600/15 px-2 py-0.5 text-[11px] font-medium text-indigo-800 dark:text-indigo-200">
                {tx("mcpRecommended", "Recommended")}
              </span>
            ) : null}
            <span
              className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
              data-testid="career-linkedin-mcp-status"
            >
              {linkedinMcpOn
                ? tx("mcpEnabled", "Session enabled")
                : tx("mcpOff", "Not enabled yet")}
            </span>
          </div>
          <p className="text-pretty text-sm text-muted-foreground">{linkedin.role}</p>
          <p className="text-[12px] text-muted-foreground">
            {tx(
              "mcpLoginHint",
              "First use opens a LinkedIn login, or run uvx mcp-server-linkedin@latest --login / --import-from-browser.",
            )}
          </p>
          {linkedin.jobs?.length ? (
            <p className="font-mono text-[12px] text-muted-foreground">
              {tx("mcpJobs", "Jobs")}: {linkedin.jobs.join(" · ")}
            </p>
          ) : null}
          {linkedin.confirm?.length ? (
            <p className="text-[12px] text-muted-foreground">
              {tx("mcpConfirmNote", "Needs your confirmation")}: {linkedin.confirm.join(" · ")}
            </p>
          ) : null}
        </div>
      ) : null}
      {others.length ? (
        <ul className="grid gap-1 text-sm text-muted-foreground">
          {others.map((row) => (
            <li key={row.id || row.name}>
              <span className="font-medium text-foreground">{row.name || row.id}</span>
              {" - "}
              {row.role}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function OfficialPortalPack({
  tx,
  portals,
  token,
}: {
  tx: Tx;
  portals: OfficialPortal[];
  token: string;
}) {
  const linkedin = portals.filter((row) => row.kind === "linkedin").slice(0, 4);
  const boards = portals.filter((row) => row.kind !== "linkedin").slice(0, 5);
  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm font-semibold">{tx("linkedinPack", "Open LinkedIn")}</p>
        <p className="mt-1 text-pretty text-sm text-muted-foreground">
          {tx(
            "linkedinPackHint",
            "Opens your LinkedIn session. Copy the title and description, then import below. Navin never applies for you.",
          )}
        </p>
        <div className="mt-3 flex flex-wrap gap-3">
          {linkedin.map((portal) => (
            <DefaultButton
              key={portal.id}
              styles={BUTTON_STYLES}
              text={portal.label}
              iconProps={{ iconName: "NavigateExternalInline" }}
              onClick={() => openOfficialCareerUrl(token, portal.url)}
            />
          ))}
        </div>
      </div>
      <div>
        <p className="text-sm font-semibold">{tx("openPortals", "Open official boards")}</p>
        <div className="mt-3 flex flex-wrap gap-3">
          {boards.map((portal) => (
            <DefaultButton
              key={portal.id}
              styles={BUTTON_STYLES}
              text={portal.label}
              iconProps={{ iconName: "NavigateExternalInline" }}
              onClick={() => openOfficialCareerUrl(token, portal.url)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ImportOfferForm({
  tx,
  onImport,
}: {
  tx: Tx;
  onImport: (raw: { url: string; title: string; company: string; body: string }) => void;
}) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [body, setBody] = useState("");
  return (
    <details open className="rounded-xl p-4 outline outline-1 outline-black/10 dark:outline-white/10">
      <summary className="cursor-pointer text-sm font-semibold">
        {tx("importToggle", "Import a LinkedIn offer (paste)")}
      </summary>
      <div className="mt-3 space-y-3">
      <p className="text-pretty text-sm text-muted-foreground">
        {tx(
          "importHint",
          "Open the official page, copy the URL, title and description. Navin stores your paste and scores it.",
        )}
      </p>
      <TextField
        label={tx("importUrl", "Official URL")}
        value={url}
        onChange={(_, value) => setUrl(value || "")}
        placeholder="https://www.linkedin.com/jobs/view/..."
      />
      <div className="grid gap-2 sm:grid-cols-2">
        <TextField
          label={tx("importJobTitle", "Title")}
          value={title}
          onChange={(_, value) => setTitle(value || "")}
        />
        <TextField
          label={tx("importCompany", "Company")}
          value={company}
          onChange={(_, value) => setCompany(value || "")}
        />
      </div>
      <TextField
        label={tx("importBody", "Description copied from the page")}
        multiline
        rows={4}
        value={body}
        onChange={(_, value) => setBody(value || "")}
      />
      <DefaultButton
        styles={BUTTON_STYLES}
        iconProps={{ iconName: "Add" }}
        text={tx("importSubmit", "Import and score")}
        disabled={!url.trim() && !title.trim() && !body.trim()}
        onClick={() => {
          onImport({ url, title, company, body });
          setUrl("");
          setTitle("");
          setCompany("");
          setBody("");
        }}
      />
      </div>
    </details>
  );
}

function DeskSkeleton() {
  return (
    <div className="mx-auto mb-4 grid w-full max-w-5xl gap-3" aria-hidden>
      <div className="h-20 animate-pulse rounded-2xl bg-muted/50" />
      <div className="h-36 animate-pulse rounded-2xl bg-muted/40" />
    </div>
  );
}

const CARD_FACT_ICON: Record<string, string> = {
  start: "Calendar",
  duration: "Clock",
  salary: "Money",
  dayrate: "Money",
  pay: "Money",
  remote: "Home",
  place: "MapPin",
  experience: "Contact",
};

function regionName(code: string, locale: string): string {
  const iso = String(code || "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(iso)) return code;
  try {
    return new Intl.DisplayNames([locale], { type: "region" }).of(iso) || iso;
  } catch {
    return iso;
  }
}

function postedLabel(value: string | undefined, locale: string): string {
  const day = offerIsoDay(value);
  if (!day) return "";
  try {
    return new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(`${day}T12:00:00Z`));
  } catch {
    return day;
  }
}

function companyInitials(name: string): string {
  const words = String(name || "").replace(/[^\p{L}\p{N} ]+/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return words.slice(0, 2).map((word) => word[0]?.toUpperCase() || "").join("");
}

/** Board-style offer card: pills, title, tags, company excerpt on the left, the facts column on the right. */
function OfferCard({
  row,
  tx,
  copy,
  locale,
  track,
  hints,
  open,
  listView,
  onSelect,
  onOfferAction,
}: {
  row: CareerOpportunity;
  tx: Tx;
  copy: OfferFactCopy;
  locale: string;
  track: CareerTrack;
  hints: string[];
  open: boolean;
  listView: OfferListView;
  onSelect: (id: string) => void;
  onOfferAction: (id: string, action: OfferRowAction) => void;
}) {
  const starred = Boolean(row.favorite) && !row.archived;
  const facts = offerFacts(row, copy, locale, track);
  const byId = new Map(facts.map((fact) => [fact.id, fact]));
  const known = (id: string) => {
    const fact = byId.get(id);
    return fact && fact.value !== copy.unknown ? fact : null;
  };
  const side: { id: string; label: string; value: string }[] = [];
  for (const id of ["start", "duration", "salary", "dayrate", "pay", "remote"]) {
    const fact = known(id);
    if (fact) side.push(fact);
  }
  const city = known("city")?.value || "";
  const country = known("country")?.value || "";
  const place = [city, country ? regionName(country, locale) : ""].filter(Boolean).join(", ");
  if (place) side.push({ id: "place", label: tx("factPlace", "Location"), value: place });
  const experience = known("experience");
  if (experience) side.push(experience);
  const domain = offerDomain(row, hints);
  const tags = [...new Set([domain, ...(row.stack || []).map((item) => String(item).trim())].filter(Boolean))].slice(0, 3);
  const posted = postedLabel(row.posted_at, locale);
  const excerpt = stripHtml(row.description || "").replace(/\s+/g, " ").trim().slice(0, 240);
  const company = row.company || row.source || "";
  return (
    <article
      data-testid="career-offer-card"
      className={cn(
        "grid overflow-hidden rounded-2xl bg-background outline outline-1 outline-black/10 transition-shadow duration-150 hover:shadow-[0_8px_24px_rgba(15,23,42,0.08)] dark:outline-white/10 md:grid-cols-[minmax(0,1fr)_15.5rem]",
        open && "outline-indigo-500/70 ring-2 ring-indigo-500/20",
      )}
    >
      <div className="min-w-0 p-4 sm:p-5">
        <div className="flex items-start gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
            <OfferBadges row={row} copy={copy} />
            {row.archived ? (
              <span className="rounded-full bg-slate-500/15 px-2 py-0.5 text-[11px] font-medium">{tx("archivedBadge", "Archived")}</span>
            ) : null}
          </div>
          <span
            className={cn("shrink-0 tabular-nums text-sm font-semibold", scoreTone(row.match_score))}
            title={tx("filterBucket", "Match")}
          >
            {row.match_score ?? "-"}%
          </span>
          <IconButton
            ariaLabel={starred ? tx("unfavorite", "Remove favorite") : tx("favorite", "Favorite")}
            title={starred ? tx("unfavorite", "Remove favorite") : tx("favorite", "Favorite")}
            iconProps={{ iconName: starred ? "HeartFill" : "Heart" }}
            onClick={() => onOfferAction(row.id, starred ? "unfavorite" : "favorite")}
            styles={{ root: { width: 32, height: 32, color: starred ? "rgb(225 29 72)" : undefined }, rootHovered: { color: "rgb(225 29 72)" } }}
            data-testid="career-offer-favorite"
          />
          <IconButton
            ariaLabel={tx("rowMenu", "Offer actions")}
            title={tx("rowMenu", "Offer actions")}
            iconProps={{ iconName: "MoreVertical" }}
            menuProps={{ ...OFFER_ROW_MENU, items: offerMenuItems(listView, starred, tx, (action) => onOfferAction(row.id, action)) }}
            styles={{ root: { width: 32, height: 32 }, menuIcon: { display: "none" } }}
          />
        </div>
        <button type="button" className="mt-2 block w-full text-left" onClick={() => onSelect(row.id)}>
          <h3 className="text-balance text-lg font-semibold leading-snug hover:underline">{row.title}</h3>
        </button>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {tags.map((tag) => (
            <span key={tag} className="rounded-md bg-amber-300/30 px-1.5 py-0.5 text-[11px] font-medium text-amber-900 dark:text-amber-200">
              {tag}
            </span>
          ))}
          {posted ? <span className="ml-auto text-xs tabular-nums text-muted-foreground">{posted}</span> : null}
        </div>
        <div className="mt-3 flex gap-3">
          <div
            aria-hidden
            className="grid size-14 shrink-0 place-items-center rounded-xl bg-indigo-500/12 text-base font-bold text-indigo-700 dark:text-indigo-300"
          >
            {companyInitials(company)}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{company}</p>
            <p className="mt-0.5 line-clamp-3 text-pretty text-sm text-muted-foreground">
              {excerpt || tx("noDescription", "No description stored. Open the original URL.")}
            </p>
          </div>
        </div>
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            className="text-sm font-semibold text-indigo-700 underline-offset-2 hover:underline dark:text-indigo-300"
            onClick={() => onSelect(row.id)}
            data-testid="career-offer-open"
          >
            {tx("viewOffer", "View this offer")}
          </button>
        </div>
      </div>
      <aside className="border-t border-black/10 bg-muted/30 p-4 dark:border-white/10 md:border-l md:border-t-0">
        <dl className="grid gap-3" data-testid="career-offer-facts">
          {side.map((fact) => (
            <div key={fact.id}>
              <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{fact.label}</dt>
              <dd className="mt-0.5 flex items-center gap-1.5 text-sm">
                <Icon iconName={CARD_FACT_ICON[fact.id] || "Info"} className="text-[13px] text-indigo-600 dark:text-indigo-300" aria-hidden />
                <span className="min-w-0 break-words">{fact.value}</span>
              </dd>
            </div>
          ))}
          {!side.length ? <p className="text-sm text-muted-foreground">{copy.unknown}</p> : null}
        </dl>
      </aside>
    </article>
  );
}

function OffersPane({
  tx,
  rows,
  allRows,
  domainHints,
  selected,
  onSelect,
  onOfferAction,
  archiveAfterDays,
  deleteAfterDays,
  searchNote,
  token,
  busy,
  onSearch,
  onPrepare,
  onApply,
  onEmail,
  onMarkApplied,
  onDownload,
  onCopy,
  copied,
  bucket,
  onBucket,
  listView,
  onListView,
  locale = "fr-FR",
  displayName,
  track = "freelance",
}: {
  tx: Tx;
  rows: CareerOpportunity[];
  allRows: CareerOpportunity[];
  domainHints: string[];
  selected: CareerOpportunity | null;
  onSelect: (id: string) => void;
  onOfferAction: (id: string, action: OfferRowAction) => void;
  archiveAfterDays: number;
  deleteAfterDays: number;
  searchNote?: string;
  token: string;
  busy: boolean;
  onSearch: () => void;
  onPrepare: () => void;
  onApply: () => void;
  onEmail: () => void;
  onMarkApplied: () => void;
  onDownload: (id: string, kind?: "cv_docx" | "cover_docx") => void;
  onCopy: (text: string) => void;
  copied: string;
  bucket: "all" | "perfect" | "good" | "skip";
  onBucket: (value: "all" | "perfect" | "good" | "skip") => void;
  listView: OfferListView;
  onListView: (view: OfferListView) => void;
  locale?: string;
  displayName?: string;
  track?: CareerTrack;
}) {
  const [page, setPage] = useState(1);
  const [facet, setFacet] = useState<OfferFacetFilter>(emptyOfferFilter);
  const favorites = useMemo(() => favoriteOffers(allRows), [allRows]);
  const archived = useMemo(() => archivedOffers(allRows), [allRows]);
  const pool = listView === "favorites" ? favorites : listView === "archive" ? archived : rows;
  const filtered = useMemo(() => applyOfferFilter(pool, facet, domainHints), [domainHints, facet, pool]);
  const pages = offerPageCount(filtered.length);
  const selectedIndex = selected ? filtered.findIndex((row) => row.id === selected.id) : -1;
  const wantedPage = offerPageOf(selectedIndex);
  const safePage = Math.min(page, pages);
  const pageRows = useMemo(() => paginateOffers(filtered, safePage), [filtered, safePage]);
  useEffect(() => {
    if (selectedIndex >= 0) {
      setPage(wantedPage);
      return;
    }
    setPage(1);
  }, [listView, bucket, facet, filtered.length, selectedIndex, wantedPage]);
  const openList = (next: OfferListView) => {
    onListView(next);
    setPage(1);
  };

  const copy = factsCopy(tx);
  const listTabs = (
    <div className="flex shrink-0 flex-nowrap items-center gap-2">
      <div className="flex flex-nowrap gap-1.5" role="group" aria-label={tx("bucketAria", "Match filters")}>
        {(["all", "perfect", "good", "skip"] as const).map((id) => (
          <button
            key={id}
            type="button"
            className={cn(
              "min-h-8 rounded-full px-3 text-xs font-medium outline outline-1 transition-[background-color,color] duration-150",
              bucket === id
                ? "bg-indigo-600 text-white outline-indigo-600"
                : "outline-black/10 hover:bg-muted/50 dark:outline-white/10",
            )}
            onClick={() => onBucket(id)}
          >
            {tx(`bucket.${id}`, id)}
          </button>
        ))}
      </div>
      <div className="flex flex-nowrap gap-1.5" role="tablist" aria-label={tx("listViews", "Offer lists")}>
        {(
          [
            { id: "inbox" as const, label: "resultsTitle", fallback: "Offers", count: rows.length },
            { id: "favorites" as const, label: "openFavorites", fallback: "Favorites", count: favorites.length },
            { id: "archive" as const, label: "openArchive", fallback: "Archive", count: archived.length },
          ]
        ).map((item) => {
          const active = listView === item.id;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => openList(item.id)}
              className={cn(
                "min-h-8 cursor-pointer rounded-full px-3 text-xs font-medium outline outline-1 transition-[background-color,color] duration-150",
                active
                  ? "bg-indigo-600 text-white outline-indigo-600"
                  : "outline-black/10 hover:bg-muted/50 dark:outline-white/10",
              )}
            >
              {tx(item.label, item.fallback)} · {item.count}
            </button>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-5" data-testid="career-offers">
      {searchNote ? (
        <details className="text-sm text-muted-foreground">
          <summary className="cursor-pointer select-none text-xs font-medium">{tx("searchNoteTitle", "Sources of this run")}</summary>
          <p className="mt-2 text-pretty">{searchNote}</p>
        </details>
      ) : null}

      {pool.length === 0 ? (
        <Surface className="space-y-4 p-6">
          <h3 className="text-balance text-lg font-semibold">
            {listView === "favorites"
              ? tx("favoritesEmptyTitle", "No favorites yet")
              : listView === "archive"
                ? tx("archiveEmptyTitle", "Archive is empty")
                : tx("resultsTitle", "Offers")}
          </h3>
          <p className="max-w-xl text-pretty text-sm text-muted-foreground">
            {listView === "favorites"
              ? tx("favoritesEmptyBody", "Open a row menu and choose Favorite to build this list.")
              : listView === "archive"
                ? tx(
                    "archiveEmptyBody",
                    "Offers are archived after {{archive}} days and deleted after {{purge}} days.",
                    { archive: archiveAfterDays, purge: deleteAfterDays },
                  )
                : tx(
                    "emptyOffers",
                    "No offers yet. Use Find me a mission, or paste an offer from the Profile pane.",
                  )}
          </p>
          {listView === "inbox" ? (
            <PrimaryButton
              styles={BUTTON_STYLES}
              iconProps={{ iconName: "Search" }}
              text={tx("search", "Search offers")}
              onClick={onSearch}
            />
          ) : (
            <DefaultButton
              styles={BUTTON_STYLES}
              text={tx("backOffers", "Back to offers")}
              onClick={() => openList("inbox")}
            />
          )}
        </Surface>
      ) : (
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4">
          <CareerFilters
            rows={pool}
            hints={domainHints}
            filter={facet}
            tx={tx}
            track={track}
            locale={locale}
            onChange={setFacet}
            leading={listTabs}
            trailing={
              <IconButton
                iconProps={{ iconName: "Download" }}
                disabled={!filtered.length}
                title={
                  filtered.length
                    ? tx("exportHint", "Exports every filtered offer, not only this page.")
                    : tx("exportEmpty", "Nothing to export. Change the filters.")
                }
                ariaLabel={tx("export", "Export")}
                menuProps={{
                  ...OFFER_ROW_MENU,
                  items: [
                    {
                      key: "csv",
                      text: tx("exportCsv", "CSV"),
                      iconProps: { iconName: "TextDocument" },
                      disabled: !filtered.length,
                      onClick: () => downloadCareerExport(filtered, "csv", { locale, tx }),
                    },
                    {
                      key: "xlsx",
                      text: tx("exportExcel", "Excel"),
                      iconProps: { iconName: "ExcelDocument" },
                      disabled: !filtered.length,
                      onClick: () => downloadCareerExport(filtered, "xlsx", { locale, tx }),
                    },
                  ],
                }}
                data-testid="career-export"
                styles={ICON_BUTTON_STYLES}
              />
            }
          />
          {listView !== "inbox" ? (
            <p className="max-w-xl text-pretty text-sm text-muted-foreground">
              {listView === "favorites"
                ? tx(
                    "favoritesHint",
                    "Starred offers stay here until you archive or delete them. Auto-archive still runs after {{days}} days.",
                    { days: archiveAfterDays },
                  )
                : tx(
                    "archiveHint",
                    "Offers move here after {{archive}} days, or when you archive them. They are deleted after {{purge}} days.",
                    { archive: archiveAfterDays, purge: deleteAfterDays },
                  )}
            </p>
          ) : null}
          <p className="text-sm text-muted-foreground" data-testid="career-result-count">
            {tx("resultCount", "Your search returns {{count}} results.", { count: filtered.length })}
          </p>
          {filtered.length === 0 ? (
            <Surface className="space-y-3 p-6">
              <h3 className="text-balance text-lg font-semibold">
                {tx("filterEmptyTitle", "Nothing in this view")}
              </h3>
              <p className="max-w-xl text-pretty text-sm text-muted-foreground">
                {tx("filterEmptyBody", "No offer matches this filter or search.")}
              </p>
              <DefaultButton
                styles={BUTTON_STYLES}
                text={tx("clearFilters", "Clear filters")}
                onClick={() => setFacet(emptyOfferFilter())}
              />
            </Surface>
          ) : null}
          <div className="grid gap-3" data-testid="career-offer-grid">
            {pageRows.map((row) => (
              <OfferCard
                key={row.id}
                row={row}
                tx={tx}
                copy={copy}
                locale={locale}
                track={track}
                hints={domainHints}
                open={selected?.id === row.id}
                listView={listView}
                onSelect={onSelect}
                onOfferAction={onOfferAction}
              />
            ))}
          </div>
          <Panel
            isOpen={Boolean(selected)}
            isLightDismiss
            type={PanelType.medium}
            headerText={selected?.title || tx("resultsTitle", "Offers")}
            closeButtonAriaLabel={tx("appsClose", "Close")}
            onDismiss={() => onSelect("")}
          >
            {selected ? (
              <OfferDetail
                tx={tx}
                row={selected}
                copy={factsCopy(tx)}
                locale={locale}
                track={track}
                token={token}
                busy={busy}
                copied={copied}
                displayName={displayName}
                onPrepare={onPrepare}
                onApply={onApply}
                onEmail={onEmail}
                onMarkApplied={onMarkApplied}
                onDownload={onDownload}
                onCopy={onCopy}
              />
            ) : null}
          </Panel>
          {filtered.length ? (
            <OfferPager
              page={safePage}
              pages={pages}
              count={filtered.length}
              pageSize={CAREER_PAGE_SIZE}
              tx={tx}
              onPage={setPage}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}

function CareerSourcesBlock({
  tx,
  catalog,
  mcp,
  linkedinMcpOn,
  portals,
  token,
  onImport,
  employers,
  userEmployers = [],
  hiddenEmployers = [],
  busy,
  onSaveProfile,
}: {
  tx: Tx;
  catalog: CareerSource[];
  mcp: CareerMcpHint[];
  linkedinMcpOn?: boolean;
  portals: OfficialPortal[];
  token: string;
  onImport: (raw: { url: string; title: string; company: string; body: string }) => void;
  employers?: CareerEmployersPayload;
  userEmployers?: CareerUserEmployer[];
  hiddenEmployers?: string[];
  busy?: boolean;
  onSaveProfile?: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="grid gap-8">
      {onSaveProfile ? (
        <Surface className="p-6">
          <CareerEmployers
            tx={tx}
            employers={employers}
            userEmployers={userEmployers}
            hidden={hiddenEmployers}
            token={token}
            busy={busy}
            onSaveProfile={onSaveProfile}
          />
        </Surface>
      ) : null}
      <Surface className="space-y-5 p-6">
        <CareerMcpOptions tx={tx} rows={mcp} linkedinMcpOn={linkedinMcpOn} />
        <OfficialPortalPack tx={tx} portals={portals} token={token} />
        <ImportOfferForm tx={tx} onImport={onImport} />
      </Surface>
      {catalog.length ? (
        <details className={cn(SURFACE, "p-5")} data-testid="career-sources">
          <summary className="cursor-pointer text-sm font-semibold">
            {tx("sourcesTitle", "Authorized sources")}
          </summary>
          <p className="mt-3 text-pretty text-sm text-muted-foreground">
            {tx(
              "catalogHint",
              "Skills and official MCP live in Skills and Tools. The agent loads them there. This list is only the public boards you can open.",
            )}
          </p>
          <ul className="mt-3 space-y-2">
            {catalog.map((source) => (
              <li key={source.id}>
                <a
                  className="text-sm text-indigo-700 underline-offset-2 hover:underline dark:text-indigo-300"
                  href={source.url}
                  data-open-url={source.url}
                  onClick={(event) => {
                    event.preventDefault();
                    openOfficialCareerUrl(token, source.url);
                  }}
                >
                  {source.name}
                </a>
                <span className="text-sm text-muted-foreground"> - {source.notes}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function offerMenuItems(
  view: OfferListView,
  starred: boolean,
  tx: Tx,
  onAction: (action: OfferRowAction) => void,
): IContextualMenuItem[] {
  const viewItem: IContextualMenuItem = {
    key: "view",
    text: tx("rowView", "View"),
    iconProps: { iconName: "View" },
    onClick: () => onAction("view"),
  };
  const deleteItem: IContextualMenuItem = {
    key: "delete",
    text: tx("rowDelete", "Delete"),
    iconProps: { iconName: "Delete" },
    onClick: () => onAction("delete"),
  };
  if (view === "archive") {
    return [
      viewItem,
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

function OfferPager({
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
        {tx("pageStatus", "Page {{page}} / {{pages}} · {{count}} offer(s) · {{size}} per page", {
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
        {tx("pageStatus", "Page {{page}} / {{pages}} · {{count}} offer(s) · {{size}} per page", {
          page,
          pages,
          count,
          size: pageSize,
        })}
      </p>
      <div className="flex flex-wrap gap-2">
        <DefaultButton
          text={tx("pagePrev", "Previous")}
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          styles={BUTTON_STYLES}
        />
        <DefaultButton
          text={tx("pageNext", "Next")}
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          styles={BUTTON_STYLES}
        />
      </div>
    </div>
  );
}

function cvLabels(tx: Tx, language?: string) {
  const options = language ? { lng: language } : undefined;
  return {
    profile: tx("cvProfile", "Profile", options),
    skills: tx("cvSkills", "Skills", options),
    strengths: tx("cvStrengths", "Strengths", options),
    highlights: tx("cvHighlights", "Selected achievements", options),
    experience: tx("cvExperience", "Experience", options),
    education: tx("cvEducation", "Education", options),
    languages: tx("cvLanguages", "Languages", options),
    letter: tx("coverLetter", "Cover letter", options),
  };
}

function ApplicationsPane({
  tx,
  desk,
  apps,
  jobs,
  track,
  pipelineCount,
  locale = "fr-FR",
  token,
  onPrepare,
  onApply,
  onEmail,
  onMarkApplied,
  onDownload,
  onOpenDiscover,
  onCopy,
}: {
  tx: Tx;
  desk: CareerDesk;
  apps: CareerApplication[];
  jobs: CareerOpportunity[];
  track: CareerTrack;
  pipelineCount: number;
  locale?: string;
  token: string;
  onPrepare: (id: string) => void;
  onApply: (id: string) => void;
  onEmail: (id: string) => void;
  onMarkApplied: (id: string) => void;
  onDownload: (id: string, kind?: "cv_docx" | "cover_docx") => void;
  onOpenDiscover: () => void;
  onCopy: (text: string) => void;
}) {
  const [openId, setOpenId] = useState("");
  const copy = factsCopy(tx);
  const unknown = copy.unknown;
  const mergedJobs = [...jobs, ...(desk.opportunities || [])];
  const rows = listTrackedApplications(apps, mergedJobs, track);
  const open = rows.find((row) => row.app.id === openId || row.app.opportunity_id === openId) || null;

  if (!rows.length) {
    return (
      <Surface className="space-y-4 p-6" data-testid="career-applications">
        <h2 className="text-balance text-xl font-semibold">{tx("pane.applications", "Applications")}</h2>
        <p className="max-w-2xl text-pretty text-sm text-muted-foreground">
          {tx(
            "appsEmptyBody",
            "This tab tracks missions you actually started: tailored pack, employer page opened, or application sent. It is not the list of jobs Navin found.",
          )}
        </p>
        {pipelineCount > 0 ? (
          <p className="max-w-2xl text-pretty text-sm text-muted-foreground">
            {tx(
              "appsEmptyPipeline",
              "Pipeline ({{count}} offers) and Offers list jobs that were found. They become an application only after you prepare a pack or mark one as sent.",
              { count: pipelineCount },
            )}
          </p>
        ) : (
          <p className="max-w-2xl text-pretty text-sm text-muted-foreground">
            {tx("noApps", "No applications yet. Prepare a pack from Offers.")}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <PrimaryButton
            styles={BUTTON_STYLES}
            text={tx("appsPrepareCta", "Prepare a pack from an offer")}
            onClick={onOpenDiscover}
            data-testid="career-apps-prepare-cta"
          />
          <DefaultButton
            styles={BUTTON_STYLES}
            text={tx("emptyAppsCta", "Go to offers")}
            onClick={onOpenDiscover}
          />
        </div>
      </Surface>
    );
  }

  return (
    <div className="grid gap-6" data-testid="career-applications">
      <Surface className="space-y-2 p-6">
        <h2 className="text-balance text-xl font-semibold">{tx("pane.applications", "Applications")}</h2>
        <p className="max-w-2xl text-pretty text-sm text-muted-foreground">
          {tx(
            "appsHint",
            "Packs prepared and applications sent. Pipeline stays the scored list of jobs found.",
          )}
        </p>
      </Surface>
      {rows.map((view) => {
        const job = view.job;
        const facts = job ? offerFacts(job, copy, locale, track) : [];
        const when = formatCareerDate(view.appliedAt || view.updatedAt, locale, unknown);
        return (
          <Surface key={view.app.id} className="p-5 sm:p-6">
            <button
              type="button"
              className="w-full min-w-0 text-left"
              onClick={() => setOpenId(view.app.opportunity_id)}
              data-testid="career-application-row"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-balance text-lg font-semibold">{view.title}</p>
                  <p className="mt-1 text-pretty text-sm text-muted-foreground">
                    {[view.company, view.source, applicationStatusLabel(view, tx)].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <span className="shrink-0 rounded-full bg-indigo-500/15 px-2.5 py-1 text-xs font-medium text-indigo-800 dark:text-indigo-200">
                  {applicationStatusLabel(view, tx)}
                </span>
              </div>
              {facts.length ? <OfferFactsRow facts={facts} /> : null}
              <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
                <div>
                  <dt className="text-[11px] font-medium text-muted-foreground">{tx("appsDate", "Date")}</dt>
                  <dd>{when}</dd>
                </div>
                <div>
                  <dt className="text-[11px] font-medium text-muted-foreground">{tx("appsSource", "Source")}</dt>
                  <dd>{view.source || unknown}</dd>
                </div>
                <div>
                  <dt className="text-[11px] font-medium text-muted-foreground">{tx("appsNext", "Next action")}</dt>
                  <dd className="text-pretty">{view.nextAction || unknown}</dd>
                </div>
              </dl>
            </button>
            <div className="mt-4 flex flex-wrap gap-2">
              <DefaultButton
                styles={BUTTON_STYLES}
                text={locale.startsWith("fr") ? "Courrier de candidature" : "Application email"}
                iconProps={{ iconName: "Mail" }}
                disabled={!view.app.pack_ready}
                onClick={() => onEmail(view.app.opportunity_id)}
                data-testid="career-application-email"
              />
              <DefaultButton
                styles={BUTTON_STYLES}
                text={tx("appsOpenDetail", "Open details")}
                onClick={() => setOpenId(view.app.opportunity_id)}
              />
              {view.stage !== "applied" && view.stage !== "won" && view.stage !== "rejected" ? (
                <DefaultButton
                  styles={BUTTON_STYLES}
                  text={tx("markApplied", "Mark as sent")}
                  data-testid="career-mark-applied"
                  onClick={() => onMarkApplied(view.app.opportunity_id)}
                />
              ) : null}
            </div>
          </Surface>
        );
      })}
      <Panel
        isOpen={Boolean(open)}
        isLightDismiss
        type={PanelType.medium}
        headerText={open?.title || tx("appsDetail", "Application details")}
        closeButtonAriaLabel={tx("appsClose", "Close")}
        onDismiss={() => setOpenId("")}
      >
        {open ? (
          <ApplicationDetail
            tx={tx}
            view={open}
            copy={copy}
            locale={locale}
            track={track}
            token={token}
            unknown={unknown}
            onPrepare={onPrepare}
            onApply={onApply}
            onEmail={onEmail}
            onMarkApplied={onMarkApplied}
            onDownload={onDownload}
            onCopy={onCopy}
          />
        ) : null}
      </Panel>
    </div>
  );
}

function ApplicationDetail({
  tx,
  view,
  copy,
  locale,
  track,
  token,
  unknown,
  onPrepare,
  onApply,
  onEmail,
  onMarkApplied,
  onDownload,
  onCopy,
}: {
  tx: Tx;
  view: ApplicationView;
  copy: OfferFactCopy;
  locale: string;
  track: CareerTrack;
  token: string;
  unknown: string;
  onPrepare: (id: string) => void;
  onApply: (id: string) => void;
  onEmail: (id: string) => void;
  onMarkApplied: (id: string) => void;
  onDownload: (id: string, kind?: "cv_docx" | "cover_docx") => void;
  onCopy: (text: string) => void;
}) {
  const job = view.job;
  const app = view.app;
  const facts = job ? offerFacts(job, copy, locale, track) : [];
  const due = followupDue(view.appliedAt, 3);
  const description = stripHtml(job?.description || "");
  return (
    <div className="grid gap-4" data-testid="career-application-detail">
      <p className="text-pretty text-sm text-muted-foreground">
        {[view.company, applicationStatusLabel(view, tx), view.source].filter(Boolean).join(" · ")}
      </p>
      {facts.length ? <OfferFactsRow facts={facts} /> : null}
      <dl className="grid gap-3 text-sm">
        <div>
          <dt className="text-[11px] font-medium text-muted-foreground">{tx("appsDate", "Date")}</dt>
          <dd>{formatCareerDate(view.appliedAt || view.updatedAt, locale, unknown)}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium text-muted-foreground">{tx("appsNext", "Next action")}</dt>
          <dd className="text-pretty">{view.nextAction || unknown}</dd>
        </div>
        {due ? (
          <div>
            <dt className="text-[11px] font-medium text-muted-foreground">{tx("appsFollowup", "Follow-up")}</dt>
            <dd>
              {tx("appsFollowupJ3", "Suggested J+3")}: {formatCareerDate(due, locale, unknown)}
            </dd>
          </div>
        ) : null}
      </dl>
      <p className="max-h-48 overflow-auto whitespace-pre-wrap text-pretty text-sm leading-relaxed">
        {description || tx("noDescription", "No description stored. Open the original URL.")}
      </p>
      {app.ats_notes ? <p className="text-pretty text-sm text-muted-foreground">{app.ats_notes}</p> : null}
      <DocumentGenerationNotice generation={app.generation} />
      {app.mail_receipt ? <CareerMailReceiptView receipt={app.mail_receipt} french={locale.startsWith("fr")} /> : null}
      {app.cv ? (
        <CvPreview
          name={app.cv.name}
          headline={app.cv.headline}
          target={app.cv.target}
          contacts={app.cv.contacts}
          summary={app.cv.summary}
          skills={app.cv.skills}
          strengths={app.cv.strengths}
          highlights={app.cv.highlights}
          sections={app.cv.sections}
          experiences={app.cv.experiences}
          education={app.cv.education}
          languages={app.cv.languages}
          cover={app.cover}
          labels={cvLabels(tx, app.cv.language)}
        />
      ) : app.cv_text ? (
        <DossierPreview title={tx("cvReady", "Tailored CV")} body={app.cv_text} />
      ) : null}
      <div className="flex flex-wrap gap-2">
        <DefaultButton
          styles={BUTTON_STYLES}
          text={locale.startsWith("fr") ? "Courrier de candidature" : "Application email"}
          iconProps={{ iconName: "Mail" }}
          disabled={!app.pack_ready}
          onClick={() => onEmail(app.opportunity_id)}
        />
        <PrimaryButton
          styles={BUTTON_STYLES}
          text={tx("apply", "Apply on the site")}
          onClick={() => onApply(app.opportunity_id)}
        />
        {view.stage !== "applied" && view.stage !== "won" && view.stage !== "rejected" ? (
          <DefaultButton
            styles={BUTTON_STYLES}
            text={tx("markApplied", "Mark as sent")}
            onClick={() => onMarkApplied(app.opportunity_id)}
          />
        ) : null}
        <DefaultButton
          styles={BUTTON_STYLES}
          text={tx("prepareAgain", "Retailor CV")}
          onClick={() => onPrepare(app.opportunity_id)}
        />
        <DefaultButton
          styles={BUTTON_STYLES}
          text={tx("downloadCv", "Download tailored CV")}
          onClick={() => onDownload(app.opportunity_id)}
        />
        {app.cover ? (
          <DefaultButton
            styles={BUTTON_STYLES}
            text={tx("downloadCover", "Download cover letter")}
            onClick={() => onDownload(app.opportunity_id, "cover_docx")}
          />
        ) : null}
        {app.cover ? (
          <DefaultButton styles={BUTTON_STYLES} text={tx("copyCover", "Copy message")} onClick={() => onCopy(app.cover || "")} />
        ) : null}
        {job?.url ? (
          <DefaultButton
            styles={BUTTON_STYLES}
            text={tx("openSource", "Open original")}
            onClick={() => openOfficialCareerUrl(token, job.url || "")}
          />
        ) : null}
      </div>
    </div>
  );
}

function InboxPane({
  tx,
  items,
  followup,
  inboxText,
  setInboxText,
  selectedId,
  canFollow,
  followLabel,
  onClassify,
  onFollowup,
  onCopy,
}: {
  tx: Tx;
  items: CareerInboxItem[];
  followup: string;
  inboxText: string;
  setInboxText: (value: string) => void;
  selectedId: string;
  canFollow?: boolean;
  followLabel?: string;
  onClassify: () => void;
  onFollowup: (wave: "j3" | "j7") => void;
  onCopy: (text: string) => void;
}) {
  return (
    <div className="grid gap-8">
      <Surface className="space-y-5 p-6 sm:p-7">
        <h2 className="text-lg font-semibold" data-selected-offer={selectedId || ""}>
          {tx("pane.inbox", "Inbox")}
        </h2>
        <TextField
          label={tx("pasteReply", "Paste a recruiter reply")}
          multiline
          rows={7}
          value={inboxText}
          onChange={(_, value) => setInboxText(value || "")}
          placeholder={tx(
            "inboxPlaceholder",
            "Capgemini - Recruiter wants a call next Tuesday.",
          )}
        />
        <div className="flex flex-wrap gap-2">
          <PrimaryButton
            styles={BUTTON_STYLES}
            disabled={!inboxText.trim()}
            text={tx("classify", "Classify")}
            onClick={onClassify}
          />
          <DefaultButton
            styles={BUTTON_STYLES}
            disabled={!canFollow}
            text={tx("followup", "Draft J+3 follow-up")}
            onClick={() => onFollowup("j3")}
          />
          <DefaultButton
            styles={BUTTON_STYLES}
            disabled={!canFollow}
            text={tx("followupJ7", "Draft J+7 follow-up")}
            onClick={() => onFollowup("j7")}
          />
        </div>
        {followLabel ? (
          <p className="text-pretty text-xs text-muted-foreground">
            {tx("followTarget", "Follow-up for {{title}}", { title: followLabel })}
          </p>
        ) : null}
        {followup ? (
          <div>
            <pre className="whitespace-pre-wrap text-pretty text-sm">{followup}</pre>
            <DefaultButton
              className="mt-2"
              styles={BUTTON_STYLES}
              text={tx("copyCover", "Copy message")}
              onClick={() => onCopy(followup)}
            />
          </div>
        ) : null}
      </Surface>
      <Surface className="space-y-4 p-6 sm:p-7">
        {items.length === 0 ? (
          <p className="text-pretty text-sm text-muted-foreground">
            {tx("emptyInbox", "No classified replies yet.")}{" "}
            {tx("emptyInboxHint", "Paste a recruiter mail, then Classify. LinkedIn replies stay manual.")}
          </p>
        ) : (
          items.map((item) => (
            <article key={item.id} className="rounded-xl p-3 outline outline-1 outline-black/10 dark:outline-white/10">
              <div className="font-medium">{item.subject}</div>
              <div className="text-xs uppercase text-muted-foreground">
                {item.classification} · {item.sender}
              </div>
              <p className="mt-1 text-pretty text-sm">{item.body}</p>
            </article>
          ))
        )}
      </Surface>
    </div>
  );
}

function PipelinePane({
  tx,
  rows,
  interviewsOnly,
  onStage,
  onOpenDiscover,
  onOpen,
}: {
  tx: Tx;
  rows: CareerOpportunity[];
  interviewsOnly: boolean;
  onStage: (id: string, stage: string) => void;
  onOpenDiscover: () => void;
  onOpen: (id: string) => void;
}) {
  const stages = interviewsOnly
    ? (["interview"] as const)
    : (["discovered", "matched", "ready", "applied", "replied", "interview", "offer", "won", "rejected"] as const);
  if (!rows.length) {
    return (
      <Surface className="p-6">
        <h2 className="text-balance text-xl font-semibold">
          {interviewsOnly ? tx("pane.interviews", "Interviews") : tx("pane.pipeline", "Pipeline")}
        </h2>
        <p className="mt-2 max-w-xl text-pretty text-sm text-muted-foreground">
          {interviewsOnly
            ? tx("emptyInterviews", "No interviews yet. Move a strong match to Interview from the pipeline.")
            : tx("emptyPipeline", "Pipeline is empty. Search authorized sources, then score and prepare.")}
        </p>
        <PrimaryButton
          className="mt-4"
          styles={BUTTON_STYLES}
          text={tx("emptyAppsCta", "Go to Discover")}
          onClick={onOpenDiscover}
        />
      </Surface>
    );
  }
  return (
    <div className="space-y-8" data-testid="career-pipeline">
      {stages.map((stage) => {
        const cards = rows.filter((row) => (row.stage || "discovered") === stage);
        if (!cards.length) return null;
        return (
          <section key={stage}>
            <h3 className="mb-3 flex items-center justify-between text-sm font-semibold">
              <span>{tx(`stage.${stage}`, stage)}</span>
              <span className="tabular-nums text-xs text-muted-foreground">{cards.length}</span>
            </h3>
            <ul className="space-y-3">
              {cards.map((row) => {
                const forward = nextStage(row.stage || "discovered");
                return (
                  <li key={row.id}>
                    <article className={cn(SURFACE, "p-4")}>
                      <button type="button" className="w-full text-left" onClick={() => onOpen(row.id)}>
                        <div className="text-sm font-medium">{row.title}</div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {row.company} · <span className={scoreTone(row.match_score)}>{row.match_score ?? "-"}%</span>
                        </div>
                      </button>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {forward && forward !== stage ? (
                          <button
                            type="button"
                            className="min-h-9 rounded-full bg-indigo-500/15 px-3 text-xs"
                            onClick={() => onStage(row.id, forward)}
                          >
                            {tx("nextStage", "Next")}: {tx(`stage.${forward}`, forward)}
                          </button>
                        ) : null}
                        {stage !== "rejected" ? (
                          <button
                            type="button"
                            className="min-h-9 rounded-full px-3 text-xs outline outline-1 outline-black/10 dark:outline-white/10"
                            onClick={() => onStage(row.id, "rejected")}
                          >
                            {tx("rejectStage", "Reject")}
                          </button>
                        ) : null}
                      </div>
                    </article>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function ProfileFact({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="grid gap-1">
      <p className="text-[12px] font-medium text-muted-foreground">{label}</p>
      <p className="text-pretty text-sm leading-relaxed">{value}</p>
    </div>
  );
}

function ProfilePane({
  tx,
  track,
  profile,
  files,
  onReopen,
  busy,
  onAiAssist,
}: {
  tx: Tx;
  track: CareerTrack;
  profile: CareerProfile;
  files?: Record<string, string>;
  onReopen: () => void;
  busy: boolean;
  onAiAssist: (enabled: boolean) => void;
}) {
  const pay =
    track === "freelance"
      ? [profile.min_rate, profile.max_rate].filter((value) => Number(value) > 0).join(" - ")
      : [profile.min_salary, profile.max_salary].filter((value) => Number(value) > 0).join(" - ");
  const payLabel =
    track === "freelance"
      ? tx("wizard.rateRange", "Daily rate")
      : tx("wizard.salaryRange", "Salary range");
  return (
    <div className="grid gap-8">
      <Surface className="grid gap-6 p-6 sm:p-8">
        <div>
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
            {profile.account_kind === "company" ? tx("wizard.company", "Company") : tx("wizard.solo", "Solo")}
          </p>
          <h2 className="mt-2 text-balance text-2xl font-semibold tracking-tight">
            {profile.display_name || profile.company?.name || tx("profileBasics", "What you are looking for")}
          </h2>
          {profile.headline ? <p className="mt-2 text-pretty text-sm text-muted-foreground">{profile.headline}</p> : null}
        </div>
        <div className="grid gap-5">
          <ProfileFact label={tx("titles", "Target roles")} value={(profile.titles || []).join(" · ")} />
          <ProfileFact
            label={tx("wizard.searchCountries", "Countries to search")}
            value={(profile.countries_primary || []).join(" · ")}
          />
          <ProfileFact
            label={tx("wizard.secondaryMarkets", "Secondary markets")}
            value={(profile.countries_secondary || []).join(" · ")}
          />
          <ProfileFact
            label={tx("wizard.excludedMarkets", "Excluded markets")}
            value={(profile.countries_excluded || []).join(" · ")}
          />
          <ProfileFact
            label={tx("workMode", "Work mode")}
            value={
              profile.work_mode === "remote"
                ? tx("workRemote", "Full remote")
                : profile.work_mode === "hybrid"
                  ? tx("workHybrid", "Hybrid")
                  : profile.work_mode === "onsite"
                    ? tx("workOnsite", "On site")
                    : profile.work_mode === "any"
                      ? tx("workAny", "Any")
                      : profile.work_mode || ""
            }
          />
          <ProfileFact label={payLabel} value={pay ? `${pay} ${profile.currency || ""}`.trim() : ""} />
          <ProfileFact label={tx("available", "Available from")} value={profile.available_from || ""} />
          <ProfileFact label={tx("wizard.email", "Email")} value={profile.email || ""} />
          <ProfileFact label={tx("wizard.phone", "Phone")} value={profile.phone || ""} />
          <ProfileFact label={tx("languages", "Languages")} value={(profile.languages || []).join(" · ")} />
          <ProfileFact label={tx("visa", "Visa / sponsorship")} value={profile.visa && profile.visa !== "none" ? profile.visa : ""} />
          <ProfileFact label={tx("wizard.strengths", "Strengths to highlight")} value={(profile.strengths || []).join(" · ")} />
          <ProfileFact label={tx("wizard.weaknesses", "Gaps to watch (honest)")} value={(profile.weaknesses || []).join(" · ")} />
          <ProfileFact
            label={tx("wizard.channels", "Alerts")}
            value={
              ["email", "teams", "whatsapp", "telegram"]
                .filter((name) => profile.channels && profile.channels[name as keyof typeof profile.channels])
                .join(" · ")
            }
          />
          <ProfileFact
            label={tx("wizard.prospectMail", "Prospecting email")}
            value={
              profile.prospect_email_approved
                ? tx("wizard.mailValidated", "Email validated")
                : profile.prospect_email
                  ? tx("wizard.mailDraft", "Draft, not validated")
                  : ""
            }
          />
        </div>
        {profile.master_cv ? (
          <div className="grid gap-2">
            <p className="text-[12px] font-medium text-muted-foreground">{tx("profileCvTitle", "Master CV")}</p>
            <p className="max-h-48 overflow-auto whitespace-pre-wrap text-pretty text-sm leading-relaxed">
              {profile.master_cv}
            </p>
          </div>
        ) : null}
        <div>
          <Toggle
            label={tx("draftingAssist", "Assisted CV and cover letter drafting")}
            checked={profile.ai_assist === true}
            disabled={busy}
            onText={tx("draftingAssistOn", "Enabled")}
            offText={tx("draftingAssistOff", "Disabled")}
            onChange={(_, checked) => onAiAssist(Boolean(checked))}
          />
          <p className="text-pretty text-sm text-muted-foreground">
            {tx("draftingAssistHint", "Use the configured writing model to tailor your recorded experience to each offer. Changes apply to the next CV you generate.")}
          </p>
        </div>
        {files?.root ? (
          <div className="grid gap-2">
            <p className="text-[12px] font-medium text-muted-foreground">{tx("localBook", "Local book")}</p>
            <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
              {tx("localBookHint", "Indexed on disk. The career tool reads this first.")}
            </p>
            <p className="break-all font-mono text-[12px] leading-relaxed">{files.root}</p>
            <p className="break-all font-mono text-[12px] leading-relaxed text-muted-foreground">
              {[files.cv, files.dossier, files.index, files.book].filter(Boolean).join(" · ")}
            </p>
          </div>
        ) : null}
        <PrimaryButton
          styles={BUTTON_STYLES}
          data-testid="career-open-setup"
          text={tx("reopenSetup", tx("wizard.reopen", "Reopen setup"))}
          iconProps={{ iconName: "Settings" }}
          onClick={onReopen}
        />
      </Surface>
    </div>
  );
}
