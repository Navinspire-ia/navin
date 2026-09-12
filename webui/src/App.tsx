// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type CSSProperties,
} from "react";
import {
  Brain,
  LayoutTemplate,
  Moon,
  Repeat,
  Search,
  ShieldCheck,
  Sun,
  TriangleAlert,
  Wrench,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { DeleteConfirm } from "@/components/DeleteConfirm";
import { RenameChatDialog } from "@/components/RenameChatDialog";
import { Sidebar } from "@/components/Sidebar";
import { SessionSearchDialog } from "@/components/SessionSearchDialog";
import type { SettingsSectionKey } from "@/components/settings/SettingsView";
import type { StudioModule } from "@/components/studio/StudioWorkspace";
import { lazyWithRetry } from "@/lib/lazy-retry";
import { ThreadShell } from "@/components/thread/ThreadShell";
import { FirstRunWizard } from "@/components/onboarding/FirstRunWizard";
import { FreeSetupDialog } from "@/components/onboarding/FreeSetup";
import { HeaderUsageIndicator } from "@/components/HeaderUsageIndicator";
import { ZoomIndicator } from "@/components/ZoomIndicator";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { needsFirstRunWizard, hydrateOnboardingFromServer, markOnboardingComplete } from "@/lib/onboarding";
import { useAccount } from "@/hooks/useAccount";
import { useExternalLinkOpener } from "@/hooks/useExternalLinkOpener";

import { useSessions } from "@/hooks/useSessions";
import { useDeferredTitleRefresh } from "@/hooks/useDeferredTitleRefresh";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useSidebarState } from "@/hooks/useSidebarState";
import { useSkills } from "@/hooks/useSkills";

const DevWorkbench = lazyWithRetry(() =>
  import("@/components/dev/DevWorkbench").then((m) => ({ default: m.DevWorkbench })),
);
const MeetingWorkbench = lazyWithRetry(() =>
  import("@/components/meeting/MeetingWorkbench").then((m) => ({ default: m.MeetingWorkbench })),
);
const CrmWorkbench = lazyWithRetry(() =>
  import("@/components/crm/CrmWorkbench").then((m) => ({ default: m.CrmWorkbench })),
);
const TendersWorkspace = lazyWithRetry(() =>
  import("@/components/studio/tenders/TendersWorkspace").then((m) => ({
    default: m.TendersWorkspace,
  })),
);
const CareerWorkspace = lazyWithRetry(() =>
  import("@/components/studio/career/CareerWorkspace").then((m) => ({
    default: m.CareerWorkspace,
  })),
);
const LeadsWorkspace = lazyWithRetry(() =>
  import("@/components/studio/leads/LeadsWorkspace").then((m) => ({
    default: m.LeadsWorkspace,
  })),
);
const TradingWorkspace = lazyWithRetry(() =>
  import("@/components/studio/trading/TradingWorkspace").then((m) => ({
    default: m.TradingWorkspace,
  })),
);
const MarketingWorkspace = lazyWithRetry(() =>
  import("@/components/studio/marketing/MarketingWorkspace").then((m) => ({
    default: m.MarketingWorkspace,
  })),
);
const MontageWorkbench = lazyWithRetry(() =>
  import("@/components/montage/MontageWorkbench").then((m) => ({ default: m.MontageWorkbench })),
);
const NotesWorkbench = lazyWithRetry(() =>
  import("@/components/notes/NotesWorkbench").then((m) => ({ default: m.NotesWorkbench })),
);
const StudioWorkspace = lazyWithRetry(() =>
  import("@/components/studio/StudioWorkspace").then((m) => ({ default: m.StudioWorkspace })),
);
const SettingsView = lazyWithRetry(() =>
  import("@/components/settings/SettingsView").then((m) => ({ default: m.SettingsView })),
);
import { useLogoFallback } from "@/hooks/useLogoFallback";
import { useChatDensityAttribute } from "@/hooks/useLocalPreferences";
import { ThemeProvider, useTheme } from "@/hooks/useTheme";
import { useUiZoom } from "@/hooks/useUiZoom";
import { logoFallbackUrls } from "@/lib/provider-brand";
import { cn } from "@/lib/utils";
import {
  BootstrapAuthRequiredError,
  consumeUrlBootstrapSecret,
  consumeUrlProjectPath,
  deriveWsUrl,
  fetchBootstrap,
  loadSavedSecret,
  saveSecret,
} from "@/lib/bootstrap";
import { displayTitle, pinChatKey, placeChatKey, priorChatKeys, unpinChatKey } from "@/lib/chat-groups";
import { deriveTitle } from "@/lib/format";
import { NavinClient, type ProductModuleId } from "@/lib/navin-client";
import {
  OPEN_BOARD_TASK_EVENT,
  OPEN_CHECKPOINTS_EVENT,
  OPEN_SESSION_PLAN_EVENT,
} from "@/lib/workbench-events";
import { speakOnce } from "@/lib/tts";
import { ClientProvider, useClient } from "@/providers/ClientProvider";
import { NotificationProvider, useNotifications } from "@/providers/NotificationProvider";
import { ProductToastStack } from "@/components/ProductToastStack";
import { useProductNews } from "@/hooks/useProductNews";
import type {
  BootstrapResponse,
  ChatSummary,
  ProjectFileMatch,
  RuntimeSurface,
  PairingRequestInfo,
  SessionAutomationJob,
  SettingsPayload,
  WorkspaceScopePayload,
  WorkspacesPayload,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  checkVersion,
  createProjectFolder,
  downloadAndInstallUpdate,
  fetchPairingRequests,
  fetchSettings,
  fetchWorkspaces,
  openExternalUrl,
  runPairingAction,
  setUnauthorizedRefresher,
  updatePreferences,
  type UpdateInfo,
  type UpdateStatus,
} from "@/lib/api";
import {
  createRuntimeHost,
  toRuntimeSurface,
} from "@/lib/runtime";
import {
  findReusableEmptyChat,
  findSessionForProject,
  isListedSidebarSession,
} from "@/lib/project-session";
import {
  isNavinInternalPath,
  normalizeWorkspacePath,
  projectNameFromPath,
  sameWorkspacePath,
  selectedProjectScope,
} from "@/lib/workspace";
import {
  isEmptyShellHash,
  isEphemeralShellView,
  normalizeShellHash,
  readLastShellRouteHash,
  rememberLastShellRoute,
  shellHashesEqual,
} from "@/lib/last-shell-route";
import { isDesktopShell, isInternalDesktopUrl } from "@/lib/desktop";
import {
  dismissUpdateToast,
  nextAvailableUpdate,
  readDismissedUpdateToast,
  UPDATE_RECHECK_AFTER_HIDDEN_MS,
} from "@/lib/product-toasts";
import { isTimeoutError, isTransportError, transportMessage } from "@/lib/http";
import {
  persistRailDensity,
  railWidthFor,
  readRailDensity,
  type DevRailDensity,
} from "@/lib/dev-rail-layout";
import { shouldShowHostChrome } from "@/lib/host-chrome";
import {
  codePanelFromParam,
  legacyCodePanelForPath,
  rewriteToCodePanel,
} from "@/lib/code-panel-route";
import {
  readLastDevContext,
  rememberLastDevContext,
  resolveLastCodeOpen,
} from "@/lib/last-dev-context";
import {
  inferChatModuleFromPreview,
  normalizeChatModuleView,
  projectModuleKey,
  resolveChatOpenView,
  resolveProjectOpenView,
  viewForCreatedChat,
} from "@/lib/chat-module";
import { DESK_CHAT_COMMAND } from "@/lib/desk-chat-command";
import { writeComposerTurnMode } from "@/components/thread/ComposerModeMenu";

type BootState =
  | { status: "loading" }
  | {
      status: "error";
      message: string;
      /** Transport failure: the engine is starting or restarting, keep trying. */
      transient?: boolean;
      attempt?: number;
    }
  | { status: "auth"; failed?: boolean }
  | {
      status: "ready";
      client: NavinClient;
      token: string;
      tokenExpiresAt: number;
      modelName: string | null;
      ingressLimits: BootstrapResponse["limits"] | null;
      runtimeSurface: RuntimeSurface;
    };

const SIDEBAR_STORAGE_KEY = "navin-webui.sidebar";
// Code / Notes / Meeting used to force the sidebar closed, and that value was
// persisted like a real preference, so the rail stayed collapsed on every later
// launch. Installs carrying that value are reset once.
const SIDEBAR_AUTOCOLLAPSE_RESET_KEY = "navin-webui.sidebar.autocollapse-reset";
const SESSION_UPDATES_STORAGE_KEY = "navin-webui.sidebar.session-updates.v1";
const LEGACY_COMPLETED_RUNS_STORAGE_KEY = "navin-webui.sidebar.completed-runs.v1";
const RESTART_STARTED_KEY = "navin-webui.restartStartedAt";
const RESTART_ROUTE_KEY = "navin-webui.restartRoute";
const RESTART_ROUTE_TTL_MS = 5 * 60 * 1000;
// Cursor's agents sidebar sits around 320px: wide enough for a title plus its
// relative timestamp without truncating everything.
const SIDEBAR_WIDTH = 320;
const SIDEBAR_RAIL_WIDTH = 56;
const MOBILE_SIDEBAR_WIDTH = `min(${SIDEBAR_WIDTH}px, calc(100vw - 0.75rem))`;
const TOKEN_REFRESH_MARGIN_MS = 30_000;
const TOKEN_REFRESH_MIN_DELAY_MS = 5_000;
/** Bootstrap retries while the engine (re)starts: 1 s, 2 s, 4 s, then 8 s. */
const BOOT_RETRY_BASE_MS = 1_000;
const BOOT_RETRY_CAP_MS = 8_000;
const PAIRING_POLL_INTERVAL_MS = 5_000;
const PAIRING_DISMISS_SNOOZE_MS = 30_000;
type ShellView =
  | "chat"
  | "settings"
  | "tools"
  | "apps"
  | "automations"
  | "skills"
  | "templates"
  | "project"
  | "risklens"
  | "dev"
  | "scraping"
  | "content"
  | "marketing"
  | "montage"
  | "ads"
  | "seo"
  | "leads"
  | "tenders"
  | "career"
  | "trading"
  | "meeting"
  | "ops"
  | "notes"
  | "crm";

const WORKBENCH_VIEWS: readonly ShellView[] = [
  "scraping",
  "ads",
  "seo",
  "content",
  "marketing",
  "montage",
  "leads",
  "tenders",
  "career",
  "trading",
  "meeting",
  "risklens",
  "dev",
  "ops",
  "notes",
  "crm",
];

function isWorkbenchView(view: ShellView): boolean {
  return WORKBENCH_VIEWS.includes(view);
}

/** These desks hide the side chat until the user clicks Chat. `?chat=` in the
 *  URL is the session key, not a request to open the column. */
const DESK_CHAT_OFF_BY_DEFAULT: ReadonlySet<ShellView> = new Set([
  "tenders",
  "career",
  "trading",
  "leads",
  "marketing",
  "scraping",
  "meeting",
  "notes",
]);

/** Product module id (gateway) → the shell view that hosts it. */
const VIEW_BY_PRODUCT_MODULE: Partial<Record<ProductModuleId, ShellView>> = {
  code: "dev",
  risklens: "risklens",
  scraping: "scraping",
  content: "content",
  marketing: "marketing",
  ads: "ads",
  seo: "seo",
  leads: "leads",
  tenders: "tenders",
  career: "career",
  trading: "trading",
  meeting: "meeting",
  ops: "ops",
  notes: "notes",
  crm: "crm",
};
type ShellRoute = {
  view: ShellView;
  activeKey: string | null;
  settingsSection: SettingsSectionKey;
  /** Open the Project continuity panel inside Code (`#/code?panel=project`). */
  openProjectPanel?: boolean;
  /** Open the app-template gallery inside Code (`#/code?panel=templates`). */
  openTemplatesPanel?: boolean;
  /** Open the Evolve engine inside Code (`#/code?panel=evolve`). */
  openEvolvePanel?: boolean;
  /** Open one notice inside Tenders (`#/tenders?notice=`). Stays in the IDE. */
  tenderNotice?: string;
  /** Tenders desk pane (`#/tenders?pane=tenders`). Default is the home dashboard. */
  tenderPane?: "home" | "tenders";
  /** Career desk pane (`#/career?pane=offers`). Default is the home dashboard. */
  careerPane?: "home" | "offers";
  /** Open one offer inside Career (`#/career?job=`). */
  careerJob?: string;
  /** Leads desk pane (`#/leads?pane=book`). Default is the home dashboard. */
  leadsPane?: "home" | "book";
  /** Open one lead inside Leads (`#/leads?lead=`). */
  leadsLead?: string;
};

type PairingChannelPresentation = {
  label: string;
  initials: string;
  color: string;
  logoUrl?: string;
};

const PAIRING_CHANNEL_PRESENTATION: Record<string, PairingChannelPresentation> = {
  discord: {
    label: "Discord",
    initials: "DC",
    color: "#5865F2",
    logoUrl: "https://discord.com/favicon.ico",
  },
  email: {
    label: "Email",
    initials: "EM",
    color: "#EA4335",
    logoUrl: "https://gmail.com/favicon.ico",
  },
  matrix: {
    label: "Matrix",
    initials: "M",
    color: "#111827",
    logoUrl: "https://matrix.org/favicon.ico",
  },
  msteams: {
    label: "Microsoft Teams",
    initials: "MT",
    color: "#6264A7",
    logoUrl: "https://www.microsoft.com/favicon.ico",
  },
  signal: {
    label: "Signal",
    initials: "SG",
    color: "#3A76F0",
    logoUrl: "https://signal.org/favicon.ico",
  },
  slack: {
    label: "Slack",
    initials: "SL",
    color: "#611F69",
    logoUrl: "https://slack.com/favicon.ico",
  },
  telegram: {
    label: "Telegram",
    initials: "TG",
    color: "#229ED9",
    logoUrl: "https://telegram.org/favicon.ico",
  },
  whatsapp: {
    label: "WhatsApp",
    initials: "WA",
    color: "#25D366",
    logoUrl: "https://www.whatsapp.com/favicon.ico",
  },
};

const SETTINGS_SECTION_KEYS: SettingsSectionKey[] = [
  "overview",
  "account",
  "appearance",
  "studio",
  "providers",
  "models",
  "image",
  "video",
  "voice",
  "browser",
  "computer",
  "tools",
  "channels",
  "apps",
  "automations",
  "skills",
  "templates",
  "runtime",
  "advanced",
  "about",
];

function isSettingsSectionKey(value: string | null): value is SettingsSectionKey {
  return SETTINGS_SECTION_KEYS.includes(value as SettingsSectionKey);
}

function defaultShellRoute(): ShellRoute {
  // Cold open and empty hashes always land on Code (#/code).
  return { view: "dev", activeKey: null, settingsSection: "overview" };
}

function chatShellRoute(): ShellRoute {
  return { view: "chat", activeKey: null, settingsSection: "overview" };
}

function shellViewForSettingsSection(section: SettingsSectionKey): ShellView {
  if (section === "templates") return "templates";
  return "settings";
}

function fallbackRestartHash(hash: string): boolean {
  return isEmptyShellHash(hash);
}

function rememberRestartRoute(): void {
  if (typeof window === "undefined") return;
  try {
    const current = window.location.hash || "#/new";
    window.localStorage.setItem(RESTART_ROUTE_KEY, current);
    // Also keep the durable last-route so a cold boot after restart still lands
    // on the same module + chat even if the TTL window is missed.
    rememberLastShellRoute(current);
  } catch {
    // ignore storage errors
  }
}

function applyHashToLocation(nextHash: string): string {
  const normalized = nextHash.startsWith("#") ? nextHash : `#${nextHash}`;
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}${normalized}`,
  );
  return normalized.slice(1);
}

function maybeRestoreRestartHash(hash: string): string {
  if (typeof window === "undefined" || !fallbackRestartHash(hash)) return hash;
  try {
    const startedAt = Number(window.localStorage.getItem(RESTART_STARTED_KEY) ?? "0");
    const storedHash = window.localStorage.getItem(RESTART_ROUTE_KEY);
    if (!startedAt || !storedHash || Date.now() - startedAt > RESTART_ROUTE_TTL_MS) {
      window.localStorage.removeItem(RESTART_ROUTE_KEY);
      return hash;
    }
    window.localStorage.removeItem(RESTART_ROUTE_KEY);
    return applyHashToLocation(storedHash);
  } catch {
    return hash;
  }
}

/** Cold boot with empty hash: always open Code (#/code). */
function maybeRestoreLastShellHash(hash: string): string {
  if (typeof window === "undefined" || !fallbackRestartHash(hash)) return hash;
  return applyHashToLocation("#/code");
}

// Restore restart hash only once per page load. Empty hashes cold-boot to Code;
// `#/new` is the intentional Studio → Tchat target and must not be rewritten.
let shellHashRestoreConsumed = false;

function readShellRoute(): ShellRoute {
  if (typeof window === "undefined") return defaultShellRoute();
  const currentHash = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;
  let hash = currentHash;
  if (!shellHashRestoreConsumed) {
    shellHashRestoreConsumed = true;
    hash = maybeRestoreLastShellHash(maybeRestoreRestartHash(currentHash));
  }
  // Empty / root → Code. `#/new` is the intentional Studio → Tchat target.
  if (!hash || hash === "/") return defaultShellRoute();

  const [path, query = ""] = hash.split("?", 2);
  if (path === "/new") {
    return chatShellRoute();
  }
  const params = new URLSearchParams(query);
  const rawSettingsSection = params.get("section");
  const settingsSection = isSettingsSectionKey(rawSettingsSection)
    ? rawSettingsSection
    : "overview";
  const activeKey = params.get("chat")?.trim() || null;

  if (path === "/settings") {
    const dedicatedView = shellViewForSettingsSection(settingsSection);
    if (dedicatedView !== "settings") {
      const nextParams = new URLSearchParams();
      if (activeKey) nextParams.set("chat", activeKey);
      const qs = nextParams.toString();
      const nextHash = `#/${dedicatedView}${qs ? `?${qs}` : ""}`;
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}${nextHash}`,
      );
      return { view: dedicatedView, activeKey, settingsSection };
    }
    return {
      view: "settings",
      activeKey,
      settingsSection,
    };
  }
  if (
    path === "/tools" ||
    path === "/apps" ||
    path === "/automations" ||
    path === "/skills"
  ) {
    const section = path.slice(1) as SettingsSectionKey;
    const nextParams = new URLSearchParams();
    if (activeKey) nextParams.set("chat", activeKey);
    nextParams.set("section", section);
    const nextHash = `#/settings?${nextParams.toString()}`;
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}${nextHash}`,
    );
    return { view: "settings", activeKey, settingsSection: section };
  }
  const legacyPanel = legacyCodePanelForPath(path);
  if (legacyPanel) {
    // Project Home, the template gallery and Evolve all live inside Code now -
    // rewrite the legacy hashes in place instead of leaving them dead.
    const rewritten = rewriteToCodePanel(legacyPanel, query, activeKey);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}${rewritten.hash}`,
    );
    return {
      view: "dev",
      activeKey: rewritten.activeKey,
      settingsSection: "overview",
      openProjectPanel: legacyPanel === "project",
      openTemplatesPanel: legacyPanel === "templates",
      openEvolvePanel: legacyPanel === "evolve",
    };
  }
  if (path === "/risklens" || path === "/premortem") {
    // Canonical URL is /risklens; /premortem is a legacy alias.
    if (path === "/premortem") {
      const nextHash = `#/risklens${query ? `?${query}` : ""}`;
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}${nextHash}`,
      );
    }
    return { view: "risklens", activeKey, settingsSection: "overview" };
  }
  if (path === "/code" || path === "/dev") {
    // Canonical URL is /code; /dev is kept as a legacy alias so old
    // bookmarks keep working, and the hash is rewritten in place.
    if (path === "/dev") {
      const nextHash = `#/code${query ? `?${query}` : ""}`;
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}${nextHash}`,
      );
    }
    return {
      view: "dev",
      activeKey,
      settingsSection: "overview",
      openProjectPanel: codePanelFromParam(params.get("panel")) === "project",
      openTemplatesPanel: codePanelFromParam(params.get("panel")) === "templates",
      openEvolvePanel: codePanelFromParam(params.get("panel")) === "evolve",
    };
  }
  if (path === "/scraping" || path === "/scrape") {
    if (path === "/scrape") {
      const nextHash = `#/scraping${query ? `?${query}` : ""}`;
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}${nextHash}`,
      );
    }
    return { view: "scraping", activeKey, settingsSection: "overview" };
  }
  if (path === "/content") {
    return { view: "content", activeKey, settingsSection: "overview" };
  }
  if (path === "/marketing") {
    return { view: "marketing", activeKey, settingsSection: "overview" };
  }
  if (path === "/montage") {
    return { view: "montage", activeKey, settingsSection: "overview" };
  }
  if (path === "/ads") {
    return { view: "ads", activeKey, settingsSection: "overview" };
  }
  if (path === "/seo") {
    return { view: "seo", activeKey, settingsSection: "overview" };
  }
  if (path === "/leads") {
    const leadsLead = (params.get("lead") || "").trim();
    const book = Boolean(leadsLead) || params.get("pane") === "book";
    return {
      view: "leads",
      activeKey,
      settingsSection: "overview",
      leadsLead: leadsLead || undefined,
      leadsPane: book ? "book" : "home",
    };
  }
  if (path === "/tenders" || path.startsWith("/tenders/")) {
    const fromPath = path.startsWith("/tenders/") ? path.slice("/tenders/".length) : "";
    let tenderNotice = (params.get("notice") || "").trim();
    if (!tenderNotice && fromPath) {
      try {
        tenderNotice = decodeURIComponent(fromPath).trim();
      } catch {
        tenderNotice = fromPath.trim();
      }
    }
    return {
      view: "tenders",
      activeKey,
      settingsSection: "overview",
      tenderNotice: tenderNotice || undefined,
      tenderPane: !tenderNotice && params.get("pane") === "tenders" ? "tenders" : undefined,
    };
  }
  if (path === "/career") {
    const careerJob = (params.get("job") || "").trim();
    const rawPane = params.get("pane") || "";
    const careerOffers = Boolean(careerJob) || rawPane === "offers" || rawPane === "discover";
    return {
      view: "career",
      activeKey,
      settingsSection: "overview",
      careerJob: careerJob || undefined,
      careerPane: careerOffers ? "offers" : "home",
    };
  }
  if (path === "/trading") {
    // `#/trading` stays in the WebView on Linux, Windows and macOS.
    return { view: "trading", activeKey, settingsSection: "overview" };
  }
  if (path === "/meeting") {
    return { view: "meeting", activeKey, settingsSection: "overview" };
  }
  if (path === "/ops") {
    return { view: "ops", activeKey, settingsSection: "overview" };
  }
  if (path === "/notes") {
    return { view: "notes", activeKey, settingsSection: "overview" };
  }
  if (path === "/team") {
    // Salon Team was removed. Old bookmarks land in Code with the same chat.
    const nextHash = `#/code${query ? `?${query}` : ""}`;
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}${nextHash}`,
    );
    return { view: "dev", activeKey, settingsSection: "overview" };
  }
  if (path === "/crm" || path.startsWith("/crm/")) {
    return { view: "crm", activeKey, settingsSection: "overview" };
  }
  if (path.startsWith("/chat/")) {
    const encoded = path.slice("/chat/".length);
    try {
      const key = decodeURIComponent(encoded).trim();
      return key
        ? { view: "chat", activeKey: key, settingsSection: "overview" }
        : defaultShellRoute();
    } catch {
      return defaultShellRoute();
    }
  }
  return defaultShellRoute();
}

function shellRouteHash(route: ShellRoute): string {
  if (route.view === "chat") {
    return route.activeKey
      ? `#/chat/${encodeURIComponent(route.activeKey)}`
      : "#/new";
  }
  const params = new URLSearchParams();
  if (route.activeKey) params.set("chat", route.activeKey);
  if (
    route.view === "settings"
    && isSettingsSectionKey(route.settingsSection)
    && route.settingsSection !== "overview"
  ) {
    params.set("section", route.settingsSection);
  }
  if (route.view === "dev" && route.openProjectPanel) {
    params.set("panel", "project");
  }
  if (route.view === "dev" && route.openTemplatesPanel) {
    params.set("panel", "templates");
  }
  if (route.view === "dev" && route.openEvolvePanel) {
    params.set("panel", "evolve");
  }
  if (route.view === "tenders" && route.tenderNotice) {
    params.set("notice", route.tenderNotice);
  }
  if (route.view === "tenders" && !route.tenderNotice && route.tenderPane === "tenders") {
    params.set("pane", "tenders");
  }
  if (route.view === "career" && route.careerJob) {
    params.set("job", route.careerJob);
  } else if (route.view === "career" && route.careerPane === "offers") {
    params.set("pane", "offers");
  }
  if (route.view === "leads" && route.leadsLead) {
    params.set("lead", route.leadsLead);
  } else if (route.view === "leads" && route.leadsPane === "book") {
    params.set("pane", "book");
  }
  const query = params.toString();
  // The internal view id stays "dev" (persisted in sidebar state), but the
  // module is exposed to users as "code".
  const segment = route.view === "dev" ? "code" : route.view;
  return `#/${segment}${query ? `?${query}` : ""}`;
}

function writeShellRoute(route: ShellRoute, replace = false): void {
  if (typeof window === "undefined") return;
  const nextHash = shellRouteHash(route);
  rememberLastShellRoute(nextHash);
  if (shellHashesEqual(window.location.hash, nextHash)) return;
  if (replace) {
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}${nextHash}`,
    );
    return;
  }
  window.location.hash = nextHash;
}

function bootstrapTokenExpiresAt(expiresInSeconds: number): number {
  return Date.now() + Math.max(0, expiresInSeconds) * 1000;
}

function tokenRefreshDelayMs(expiresAt: number): number {
  const remaining = Math.max(0, expiresAt - Date.now());
  const margin = Math.min(
    TOKEN_REFRESH_MARGIN_MS,
    Math.max(1_000, remaining / 2),
  );
  return Math.max(TOKEN_REFRESH_MIN_DELAY_MS, remaining - margin);
}

function AuthForm({
  failed,
  onSecret,
}: {
  failed: boolean;
  onSecret: (secret: string) => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showSecret, setShowSecret] = useState(failed);

  useEffect(() => {
    if (failed) setShowSecret(true);
  }, [failed]);

  const openLocal = () => {
    setSubmitting(true);
    onSecret("");
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const secret = value.trim();
    if (!secret) return;
    setSubmitting(true);
    onSecret(secret);
  };

  return (
    <div className="flex h-full w-full items-center justify-center px-6">
      <div className="flex w-full max-w-sm flex-col gap-4">
        <div className="flex flex-col items-center gap-1 text-center">
          <p className="text-lg font-semibold">{t("app.auth.title")}</p>
          <p className="text-sm text-muted-foreground">{t("app.auth.hint")}</p>
        </div>
        {failed && (
          <p className="text-center text-sm text-destructive">
            {t("app.auth.invalid")}
          </p>
        )}
        <Button
          type="button"
          className="w-full"
          disabled={submitting}
          onClick={openLocal}
        >
          {t("app.auth.open")}
        </Button>
        {!showSecret ? (
          <button
            type="button"
            className="text-center text-[12px] text-muted-foreground underline-offset-2 hover:underline"
            onClick={() => setShowSecret(true)}
          >
            {t("app.auth.useSecret")}
          </button>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <Input
              type="password"
              placeholder={t("app.auth.placeholder")}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              disabled={submitting}
              autoFocus
            />
            <Button
              type="submit"
              variant="secondary"
              className="w-full"
              disabled={!value.trim() || submitting}
            >
              {t("app.auth.submit")}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}

function readSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    if (window.localStorage.getItem(SIDEBAR_AUTOCOLLAPSE_RESET_KEY) === null) {
      window.localStorage.setItem(SIDEBAR_AUTOCOLLAPSE_RESET_KEY, "1");
      window.localStorage.removeItem(SIDEBAR_STORAGE_KEY);
      return true;
    }
    const raw = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (raw === null) return true;
    return raw === "1";
  } catch {
    return true;
  }
}

function readSessionUpdateChatIds(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw =
      window.localStorage.getItem(SESSION_UPDATES_STORAGE_KEY)
      ?? window.localStorage.getItem(LEGACY_COMPLETED_RUNS_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((item): item is string => typeof item === "string"));
  } catch {
    return new Set();
  }
}

function writeSessionUpdateChatIds(chatIds: Set<string>): void {
  try {
    window.localStorage.setItem(
      SESSION_UPDATES_STORAGE_KEY,
      JSON.stringify(Array.from(chatIds)),
    );
  } catch {
    // ignore storage errors (private mode, etc.)
  }
}

function normalizeWorkspaceScope(scope: WorkspaceScopePayload): WorkspaceScopePayload {
  const accessMode = scope.access_mode === "restricted" ? "restricted" : "full";
  return {
    ...scope,
    project_name: scope.project_name ?? projectNameFromPath(scope.project_path),
    access_mode: accessMode,
    restrict_to_workspace: accessMode === "restricted",
  };
}

function isBootstrapAuthRequired(error: unknown): boolean {
  if (error instanceof BootstrapAuthRequiredError) return true;
  const msg = error instanceof Error ? error.message : String(error);
  return msg.includes("HTTP 401") || msg.includes("HTTP 403");
}

function HostChrome({
  rightAction,
}: {
  rightAction?: ReactNode;
}) {
  return (
    <header
      className={cn(
        "pointer-events-none absolute inset-x-0 top-0 z-40 h-11 bg-transparent text-foreground/90",
        typeof window !== "undefined" &&
          "navinFrameless" in window &&
          "host-drag-region",
      )}
    >
      {rightAction ? (
        <div className="host-no-drag pointer-events-auto absolute right-3 top-2">
          {rightAction}
        </div>
      ) : null}
    </header>
  );
}

function PairingCodePopup({
  requests,
  total,
  busyCode,
  error,
  onApprove,
  onDismiss,
}: {
  requests: PairingRequestInfo[];
  total: number;
  busyCode: string | null;
  error: string | null;
  onApprove: (code: string) => void;
  onDismiss: (code: string) => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const normalizedCode = normalizePairingCode(value);
  const matchedRequest = useMemo(
    () => requests.find((request) => request.code === normalizedCode) ?? null,
    [normalizedCode, requests],
  );
  const firstRequest = requests[0] ?? null;
  const displayRequest = matchedRequest ?? firstRequest;
  const expires = formatPairingExpiry(firstRequest?.expires_in_seconds);
  const isCompleteCode = normalizedCode.length === 9;
  const showNoMatch = isCompleteCode && !matchedRequest && !busyCode;

  useEffect(() => {
    if (!matchedRequest || busyCode) return;
    onApprove(matchedRequest.code);
  }, [busyCode, matchedRequest, onApprove]);

  useEffect(() => {
    if (!requests.length) setValue("");
  }, [requests.length]);

  if (!firstRequest) return null;

  return (
    <div
      role="dialog"
      aria-live="polite"
      aria-label={t("app.pairing.title", { defaultValue: "Pair a chat user" })}
      className={cn(
        "fixed right-4 top-[calc(0.75rem+env(safe-area-inset-top))] z-[70]",
        "w-[min(calc(100vw-2rem),24rem)] rounded-[24px]",
        "border border-border/70 bg-popover/95 p-4 text-popover-foreground",
        "shadow-[0_24px_70px_rgba(15,23,42,0.20)] backdrop-blur-xl",
        "animate-in fade-in-0 slide-in-from-top-2 duration-200",
      )}
    >
      <div className="flex items-start gap-3">
        <PairingChannelBadge channel={displayRequest.channel} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[15px] font-semibold tracking-[-0.01em]">
                {t("app.pairing.title", { defaultValue: "Pair a chat user" })}
              </p>
              <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
                {t("app.pairing.description", {
                  defaultValue: "Enter the pairing code shown in the chat.",
                })}
              </p>
            </div>
            <button
              type="button"
              aria-label={t("common.close", { defaultValue: "Close" })}
              onClick={() => onDismiss(firstRequest.code)}
              className="rounded-full p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>

          <label className="mt-4 block text-[12.5px] font-medium text-foreground">
            {t("app.pairing.code", { defaultValue: "Pairing code" })}
          </label>
          <PairingCodeSlots
            value={value}
            disabled={Boolean(busyCode)}
            matched={Boolean(matchedRequest)}
            invalid={showNoMatch}
            ariaLabel={t("app.pairing.code", { defaultValue: "Pairing code" })}
            onChange={(next) => setValue(formatPairingCodeInput(next))}
          />

          <div className="mt-3 flex items-center justify-between gap-3 text-[12.5px] text-muted-foreground">
            <span>
              {matchedRequest
                ? t("app.pairing.matched", {
                    defaultValue: "Matched {{channel}}. Connecting...",
                    channel: channelLabel(matchedRequest.channel),
                  })
                : t("app.pairing.expiresInline", {
                    defaultValue: "Code expires {{expires}}.",
                    expires,
                  })}
            </span>
            {total > 1 ? (
              <span className="shrink-0">
                {t("app.pairing.queueCount", {
                  defaultValue: "{{count}} pending",
                  count: total,
                })}
              </span>
            ) : null}
          </div>

          {showNoMatch ? (
            <p className="mt-2 text-[12px] leading-5 text-destructive">
              {t("app.pairing.noMatch", {
                defaultValue: "No pending request matches this code.",
              })}
            </p>
          ) : null}

          {error ? (
            <p className="mt-2 text-[12px] leading-5 text-destructive">{error}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function PairingChannelBadge({ channel }: { channel: string }) {
  const key = pairingChannelKey(channel);
  const presentation = PAIRING_CHANNEL_PRESENTATION[key];
  const label = presentation?.label ?? channelLabel(channel);
  const initials = presentation?.initials ?? label.slice(0, 2).toUpperCase();
  const color = presentation?.color ?? "#10B981";
  const logoUrls = useMemo(
    () => logoFallbackUrls(presentation?.logoUrl),
    [presentation?.logoUrl],
  );
  const { logoUrl, onLogoError, onLogoLoad } = useLogoFallback(logoUrls);

  return (
    <div
      className="mt-0.5 grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-2xl border bg-background shadow-sm"
      style={{
        borderColor: `${color}30`,
        boxShadow: `inset 0 0 0 1px ${color}14, 0 1px 2px rgba(15,23,42,0.06)`,
      }}
      aria-hidden
    >
      {logoUrl ? (
        <img
          src={logoUrl}
          alt=""
          decoding="async"
          loading="lazy"
          className="h-6 w-6 object-contain"
          onLoad={onLogoLoad}
          onError={onLogoError}
        />
      ) : presentation ? (
        <span className="text-[11px] font-bold tracking-[-0.02em]" style={{ color }}>
          {initials}
        </span>
      ) : (
        <ShieldCheck className="h-5 w-5" style={{ color }} />
      )}
    </div>
  );
}

function PairingCodeSlots({
  value,
  disabled,
  matched,
  invalid,
  ariaLabel,
  onChange,
}: {
  value: string;
  disabled: boolean;
  matched: boolean;
  invalid: boolean;
  ariaLabel: string;
  onChange: (value: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [focused, setFocused] = useState(false);
  const compact = compactPairingCode(value);
  const activeIndex = Math.min(compact.length, 7);
  const slots = Array.from({ length: 8 }, (_, index) => compact[index] ?? "");
  const renderSlot = (char: string, index: number) => {
    const highlighted = focused && index === activeIndex && !matched && !invalid;
    return (
      <div
        key={index}
        className={cn(
          "grid h-10 w-7 place-items-center rounded-xl border",
          "bg-background/80 font-mono text-[16px] font-semibold uppercase",
          "text-foreground shadow-[0_1px_1px_rgba(15,23,42,0.04)] transition",
          matched
            ? "border-emerald-500/45 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
            : invalid
              ? "border-destructive/55 bg-destructive/5 text-destructive"
              : highlighted
                ? "border-foreground/30 bg-background text-foreground"
                : char
                  ? "border-border/80 bg-background text-foreground"
                  : "border-border/55 bg-muted/35 text-muted-foreground",
        )}
      >
        {char || " "}
      </div>
    );
  };

  return (
    <div
      className={cn(
        "relative mt-2 rounded-2xl border border-transparent p-1",
        "transition duration-150",
        focused && !disabled ? "border-ring/20 bg-muted/35" : "bg-transparent",
      )}
      onClick={() => inputRef.current?.focus()}
    >
      <input
        ref={inputRef}
        value={value}
        aria-label={ariaLabel}
        inputMode="text"
        autoCapitalize="characters"
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        maxLength={9}
        disabled={disabled}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onChange={(event) => onChange(event.target.value)}
        className="absolute inset-0 z-10 h-full w-full cursor-text opacity-0 disabled:cursor-default"
      />
      <div className="pointer-events-none flex items-center gap-1.5">
        {slots.slice(0, 4).map((char, index) => renderSlot(char, index))}
        <div className="mx-0.5 h-px w-2.5 rounded-full bg-muted-foreground/35" />
        {slots.slice(4).map((char, index) => renderSlot(char, index + 4))}
      </div>
    </div>
  );
}

function compactPairingCode(raw: string): string {
  return raw.replace(/[^a-zA-Z0-9]/g, "").slice(0, 8).toUpperCase();
}

function formatPairingCodeInput(raw: string): string {
  const compact = compactPairingCode(raw);
  if (compact.length <= 4) return compact;
  return `${compact.slice(0, 4)}-${compact.slice(4)}`;
}

function normalizePairingCode(raw: string): string {
  return formatPairingCodeInput(raw);
}

function pairingChannelKey(channel: string): string {
  const raw = channel.trim().toLowerCase();
  if (!raw) return "";
  return raw.split(/[.:]/)[0] ?? raw;
}

function channelLabel(channel: string): string {
  const key = pairingChannelKey(channel);
  return PAIRING_CHANNEL_PRESENTATION[key]?.label ?? channel;
}

function formatPairingExpiry(seconds: number | null | undefined): string {
  if (seconds == null) return "soon";
  if (seconds <= 0) return "expired";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.ceil(seconds / 60)} min`;
}

export default function App() {
  const { t } = useTranslation();
  const [state, setState] = useState<BootState>({ status: "loading" });
  const bootstrapSecretRef = useRef("");

  const refreshReadyClient = useCallback(
    async (client: NavinClient, fallbackSurface: RuntimeSurface) => {
      const boot = await fetchBootstrap("", bootstrapSecretRef.current);
      const url = deriveWsUrl(boot.ws_path, boot.token, boot.ws_url);
      const runtimeSurface = boot.runtime_surface
        ? toRuntimeSurface(boot.runtime_surface)
        : fallbackSurface;
      const runtimeHost = createRuntimeHost(runtimeSurface, boot.runtime_capabilities);
      const tokenExpiresAt = bootstrapTokenExpiresAt(boot.expires_in);
      if (runtimeHost.socketFactory) {
        client.updateUrl(url, runtimeHost.socketFactory);
      } else {
        client.updateUrl(url);
      }
      setState((current) =>
        current.status === "ready" && current.client === client
          ? {
              ...current,
              token: boot.api_token,
              tokenExpiresAt,
              modelName: boot.model_name ?? current.modelName,
              ingressLimits: boot.limits ?? current.ingressLimits,
              runtimeSurface,
            }
          : current,
      );
      return { token: boot.api_token, url };
    },
    [],
  );

  const bootRetryRef = useRef<{ timer: number | null; attempt: number }>({
    timer: null,
    attempt: 0,
  });
  const bootstrapWithSecretRef = useRef<(secret: string) => () => void>(() => () => {});
  useEffect(() => {
    const retry = bootRetryRef.current;
    return () => {
      if (retry.timer !== null) window.clearTimeout(retry.timer);
    };
  }, []);

  const bootstrapWithSecret = useCallback(
    (secret: string) => {
      let cancelled = false;
      const retry = bootRetryRef.current;
      if (retry.timer !== null) {
        window.clearTimeout(retry.timer);
        retry.timer = null;
      }
      (async () => {
        setState({ status: "loading" });
        try {
          const boot = await fetchBootstrap("", secret);
          retry.attempt = 0;
          if (cancelled) return;
          if (secret) saveSecret(secret);
          const url = deriveWsUrl(boot.ws_path, boot.token, boot.ws_url);
          const runtimeSurface = toRuntimeSurface(boot.runtime_surface);
          const runtimeHost = createRuntimeHost(runtimeSurface, boot.runtime_capabilities);
          const client = new NavinClient({
            url,
            socketFactory: runtimeHost.socketFactory,
            onReauth: async () => {
              try {
                const refreshed = await refreshReadyClient(client, runtimeSurface);
                return refreshed.url;
              } catch {
                return null;
              }
            },
          });
          bootstrapSecretRef.current = secret;
          client.connect();
          setState({
            status: "ready",
            client,
            token: boot.api_token,
            tokenExpiresAt: bootstrapTokenExpiresAt(boot.expires_in),
            modelName: boot.model_name ?? null,
            ingressLimits: boot.limits ?? null,
            runtimeSurface,
          });
        } catch (e) {
          if (cancelled) return;
          if (isBootstrapAuthRequired(e)) {
            setState({ status: "auth", failed: !!secret });
          } else if (isTransportError(e)) {
            // The engine is (re)starting: keep knocking instead of parking the
            // user on a dead screen that only a reload could leave.
            retry.attempt += 1;
            setState({
              status: "error",
              message: transportMessage(isTimeoutError(e) ? "timeout" : "unreachable"),
              transient: true,
              attempt: retry.attempt,
            });
            const pauseMs = Math.min(
              BOOT_RETRY_BASE_MS * 2 ** Math.min(retry.attempt - 1, 3),
              BOOT_RETRY_CAP_MS,
            );
            retry.timer = window.setTimeout(() => {
              retry.timer = null;
              bootstrapWithSecretRef.current(secret);
            }, pauseMs);
          } else {
            setState({
              status: "error",
              message: e instanceof Error ? e.message : String(e),
            });
          }
        }
      })();
      return () => {
        cancelled = true;
      };
    },
    [refreshReadyClient],
  );
  bootstrapWithSecretRef.current = bootstrapWithSecret;

  const readyClient = state.status === "ready" ? state.client : null;
  const readyTokenExpiresAt = state.status === "ready" ? state.tokenExpiresAt : 0;
  const readySurface = state.status === "ready" ? state.runtimeSurface : null;
  useEffect(() => {
    if (!readyClient || !readySurface) return;
    const client = readyClient;
    const surface = readySurface;
    const timer = window.setTimeout(async () => {
      try {
        await refreshReadyClient(client, surface);
      } catch (e) {
        if (isBootstrapAuthRequired(e)) {
          setState({ status: "auth", failed: !!bootstrapSecretRef.current });
        }
      }
    }, tokenRefreshDelayMs(readyTokenExpiresAt));
    return () => window.clearTimeout(timer);
  }, [readyClient, readySurface, readyTokenExpiresAt, refreshReadyClient]);

  useEffect(() => {
    if (!readyClient || !readySurface) {
      setUnauthorizedRefresher(null);
      return;
    }
    const client = readyClient;
    const surface = readySurface;
    setUnauthorizedRefresher(async () => {
      try {
        const refreshed = await refreshReadyClient(client, surface);
        return refreshed.token;
      } catch {
        return null;
      }
    });
    return () => setUnauthorizedRefresher(null);
  }, [readyClient, readySurface, refreshReadyClient]);

  useEffect(() => {
    const saved = consumeUrlBootstrapSecret() || loadSavedSecret();
    return bootstrapWithSecret(saved);
  }, [bootstrapWithSecret]);

  if (state.status === "loading") {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <div className="flex flex-col items-center gap-3 animate-in fade-in-0 duration-300">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/40" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-foreground/60" />
            </span>
            {t("app.loading.connecting")}
          </div>
        </div>
      </div>
    );
  }
  if (state.status === "auth") {
    return (
      <AuthForm
        failed={!!state.failed}
        onSecret={(s) => bootstrapWithSecret(s)}
      />
    );
  }
  if (state.status === "error") {
    const desktop = isDesktopShell();
    return (
      <div className="flex h-full w-full items-center justify-center px-4 text-center">
        <div className="flex max-w-md flex-col items-center gap-3">
          <p className="text-lg font-semibold">
            {state.transient ? t("app.error.engineWaitTitle") : t("app.error.title")}
          </p>
          <p className="text-sm text-muted-foreground">{state.message}</p>
          {state.transient ? (
            <p
              className="flex items-center gap-2 text-xs text-muted-foreground"
              role="status"
              aria-live="polite"
            >
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500/50" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
              </span>
              {t("app.error.engineWait", { attempt: state.attempt ?? 1 })}
            </p>
          ) : null}
          <p className="text-xs text-muted-foreground">
            {t(desktop ? "app.error.desktopHint" : "app.error.gatewayHint")}
          </p>
          <button
            type="button"
            onClick={() => bootstrapWithSecret(bootstrapSecretRef.current)}
            className="mt-1 rounded-md border border-border/60 bg-background px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-muted/60"
          >
            {t("app.error.retryNow")}
          </button>
        </div>
      </div>
    );
  }

  const handleModelNameChange = (modelName: string | null) => {
    setState((current) =>
      current.status === "ready" ? { ...current, modelName } : current,
    );
  };

  const handleNativeEngineRestart = async (): Promise<string> => {
    const runtimeHost = createRuntimeHost(state.runtimeSurface);
    if (!runtimeHost.restartEngine) {
      throw new Error("native engine restart is unavailable");
    }
    rememberRestartRoute();
    try {
      window.localStorage.setItem(RESTART_STARTED_KEY, String(Date.now()));
    } catch {
      // ignore storage errors
    }
    try {
      await runtimeHost.restartEngine();
      const refreshed = await refreshReadyClient(state.client, state.runtimeSurface);
      return refreshed.token;
    } finally {
      try {
        window.localStorage.removeItem(RESTART_STARTED_KEY);
        window.localStorage.removeItem(RESTART_ROUTE_KEY);
      } catch {
        // ignore storage errors
      }
    }
  };

  return (
    <ClientProvider
      client={state.client}
      token={state.token}
      modelName={state.modelName}
      ingressLimits={state.ingressLimits}
    >
      <NotificationProvider>
        <Shell
          runtimeSurface={state.runtimeSurface}
          onModelNameChange={handleModelNameChange}
          onNativeEngineRestart={handleNativeEngineRestart}
        />
      </NotificationProvider>
    </ClientProvider>
  );
}

function Shell({
  runtimeSurface,
  onModelNameChange,
  onNativeEngineRestart,
}: {
  runtimeSurface: RuntimeSurface;
  onModelNameChange: (modelName: string | null) => void;
  onNativeEngineRestart: () => Promise<string>;
}) {
  const { t, i18n } = useTranslation();
  const { client, token } = useClient();
  const { theme, toggle } = useTheme();
  const uiZoom = useUiZoom();
  const {
    sessions,
    loading,
    refresh,
    noteUserMessage,
    createChat,
    forkChat,
    deleteChat,
    getSessionAutomations,
  } = useSessions();
  const { state: sidebarState, update: updateSidebarState } =
    useSidebarState(sessions, !loading);
  // The theme lives in localStorage, which the CLI cannot read. It has to know
  // it anyway: a Chromium app window takes its title bar colour from a launch
  // flag, so without this mirror the frame stays light around a dark app.
  const storedTheme = sidebarState.view.theme;
  useEffect(() => {
    if (loading || storedTheme === theme) return;
    void updateSidebarState((current) => ({
      ...current,
      view: { ...current.view, theme },
    }));
  }, [theme, storedTheme, loading, updateSidebarState]);
  const initialRouteRef = useRef<ShellRoute | null>(null);
  if (!initialRouteRef.current) initialRouteRef.current = readShellRoute();
  const [activeKey, setActiveKey] = useState<string | null>(
    initialRouteRef.current.activeKey,
  );
  const [view, setView] = useState<ShellView>(initialRouteRef.current.view);
  const [tenderNotice, setTenderNotice] = useState(
    initialRouteRef.current.tenderNotice || "",
  );
  const [tenderPane, setTenderPane] = useState<"home" | "tenders">(
    initialRouteRef.current.tenderPane === "tenders" || initialRouteRef.current.tenderNotice
      ? "tenders"
      : "home",
  );
  const [careerPane, setCareerPane] = useState<"home" | "offers">(
    initialRouteRef.current.careerPane === "offers" || initialRouteRef.current.careerJob
      ? "offers"
      : "home",
  );
  const [careerJob, setCareerJob] = useState(initialRouteRef.current.careerJob || "");
  const [leadsPane, setLeadsPane] = useState<"home" | "book">(
    initialRouteRef.current.leadsPane === "book" || initialRouteRef.current.leadsLead
      ? "book"
      : "home",
  );
  const [leadsLead, setLeadsLead] = useState(initialRouteRef.current.leadsLead || "");
  // Cold boot with a real hash (or a restored last route): keep it durable so
  // the next restart lands here even if the user never navigates again.
  useEffect(() => {
    if (!initialRouteRef.current) return;
    rememberLastShellRoute(shellRouteHash(initialRouteRef.current));
  }, []);
  // Also bind the restored chat to its module (Code/Montage/…) so sidebar
  // clicks reopen the workbench even after a cold boot that skipped navigate().
  useEffect(() => {
    const module = normalizeChatModuleView(view);
    if (!activeKey || !module || module === "chat") return;
    if (sidebarState.module_by_key[activeKey] === module) return;
    void updateSidebarState((current) => {
      if (current.module_by_key[activeKey] === module) return current;
      return {
        ...current,
        module_by_key: { ...current.module_by_key, [activeKey]: module },
      };
    });
  }, [activeKey, view, sidebarState.module_by_key, updateSidebarState]);

  const [settingsInitialSection, setSettingsInitialSection] =
    useState<SettingsSectionKey>(initialRouteRef.current.settingsSection);
  const [hostSidebarOpen, setHostSidebarOpen] =
    useState<boolean>(readSidebarOpen);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [sessionSearchOpen, setSessionSearchOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{
    key: string;
    label: string;
    automations?: SessionAutomationJob[];
  } | null>(null);
  const [pendingRename, setPendingRename] = useState<{
    key: string;
    label: string;
  } | null>(null);
  const [pendingProjectRename, setPendingProjectRename] = useState<{
    key: string;
    label: string;
  } | null>(null);
  const [pendingProjectDelete, setPendingProjectDelete] = useState<{
    key: string;
    label: string;
  } | null>(null);
  const [newProjectFolderOpen, setNewProjectFolderOpen] = useState(false);
  const restartSawDisconnectRef = useRef(false);
  const [restartToast, setRestartToast] = useState<string | null>(null);
  const [isRestarting, setIsRestarting] = useState(false);
  const [pairingRequests, setPairingRequests] = useState<PairingRequestInfo[]>([]);
  const [pairingBusyCode, setPairingBusyCode] = useState<string | null>(null);
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [snoozedPairingCodes, setSnoozedPairingCodes] = useState<Map<string, number>>(
    () => new Map(),
  );
  const [runningChatIds, setRunningChatIds] = useState<Set<string>>(() => new Set());
  const [updatedChatIds, setUpdatedChatIds] = useState<Set<string>>(readSessionUpdateChatIds);
  const [workspaces, setWorkspaces] = useState<WorkspacesPayload | null>(null);
  const { skills, refresh: refreshSkills } = useSkills(token);
  const [settingsSnapshot, setSettingsSnapshot] = useState<SettingsPayload | null>(null);
  const [showFirstRunWizard, setShowFirstRunWizard] = useState(() => needsFirstRunWizard());
  // Free onboarding path in progress: the navin account connects mid-flow,
  // which must not trigger the "install already configured" auto-complete.
  const [wizardFreeStage, setWizardFreeStage] = useState(false);
  // Free plan setup reopened after onboarding (from the provider banner).
  const [freeSetupOpen, setFreeSetupOpen] = useState(false);
  const { account, reload: reloadAccount } = useAccount();
  useExternalLinkOpener(token);
  // Fingerprint of account fields that should refresh models/providers when
  // they change (connect, logout, upgrade, downgrade, expire) - not on every poll.
  const accountSyncKeyRef = useRef<string | null>(null);
  const [availableUpdate, setAvailableUpdate] = useState<UpdateInfo | null>(null);
  const [updateBusy, setUpdateBusy] = useState(false);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);
  const updateInFlight = useRef(false);
  const tokenRef = useRef(token);
  tokenRef.current = token;
  // Update + navin.live announcements land in the notification center. The
  // installer is reached through a ref because the notification outlives the
  // render that raised it, and is defined further down with the update state.
  const { notify } = useNotifications();
  const installAvailableUpdateRef = useRef<(() => Promise<void>) | null>(null);
  useProductNews(
    availableUpdate,
    () => installAvailableUpdateRef.current?.(),
    settingsSnapshot?.updates?.justInstalled,
  );
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [draftWorkspaceScope, setDraftWorkspaceScope] =
    useState<WorkspaceScopePayload | null>(null);
  // Project directory passed from the CLI (`navin .` / `navin webui --project`).
  const [pendingCliProjectPath, setPendingCliProjectPath] = useState<string>(
    () => consumeUrlProjectPath(),
  );
  const [workspaceOverrides, setWorkspaceOverrides] =
    useState<Record<string, WorkspaceScopePayload>>({});
  const runningChatIdsRef = useRef<Set<string>>(new Set());
  const activeChatIdRef = useRef<string | null>(null);
  // Dev view: resizable chat pane (always visible - never collapsed).
  // On narrow viewports the chat becomes a bottom sheet; it still stays open.
  const isNarrowWorkbench = useMediaQuery("(max-width: 1023px)");
  // ~8% wider than the previous 460px default so submodule chats breathe.
  const [devChatWidth, setDevChatWidth] = useState(497);
  // Portal target for the artifact canvas, so rendered results show in the
  // workbench area rather than inside the chat column.
  const [artifactCanvasHost, setArtifactCanvasHost] = useState<HTMLDivElement | null>(null);
  // Focus mode: the workbench takes the whole shell and the chat column steps
  // aside. It stays mounted so the session keeps streaming underneath.
  const [workbenchFocus, setWorkbenchFocus] = useState(false);
  // Tenders / Career / Trading / Leads / Marketing / Scraping: chat column
  // hidden until the header Chat button. Independent of workbenchFocus.
  const [deskChatOpen, setDeskChatOpen] = useState(false);
  // Settings / Apps / Skills are overlays: remember the desk underneath so
  // Code (explorer, tabs, status bar) stays mounted instead of flashing a
  // blank "Loading settings" screen and remounting the whole workbench.
  const overlayReturnRouteRef = useRef<ShellRoute | null>(null);
  const settingsOverlay = isEphemeralShellView(view);
  const deskView: ShellView = settingsOverlay
    ? (
        overlayReturnRouteRef.current
        && !isEphemeralShellView(overlayReturnRouteRef.current.view)
          ? overlayReturnRouteRef.current.view
          : "dev"
      )
    : view;
  const hideSideChat =
    workbenchFocus ||
    (DESK_CHAT_OFF_BY_DEFAULT.has(deskView) && !deskChatOpen);
  // Inverse of focus mode: the workbench center pane is hidden so the
  // submodule rail (Files / Preview / Terminal / ...) stays visible on the
  // right and the chat overlays the freed center. Default for Code. The
  // workbench stays mounted (drafts, terminal, previews survive the toggle).
  // Persisted per browser: "0" is an explicit restore of the editor center.
  const [chatMaximized, setChatMaximized] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem("navin.dev.chatMaximized") !== "0";
    } catch {
      return true;
    }
  });
  const persistChatMaximized = useCallback((next: boolean) => {
    setChatMaximized(next);
    try {
      window.localStorage.setItem("navin.dev.chatMaximized", next ? "1" : "0");
    } catch {
      // localStorage unavailable: the mode still applies for this session.
    }
  }, []);
  const onToggleChatMaximized = useCallback(() => {
    persistChatMaximized(!chatMaximized);
    setWorkbenchFocus(false);
  }, [chatMaximized, persistChatMaximized]);
  const onRevealDevWorkbench = useCallback(() => {
    if (chatMaximized) persistChatMaximized(false);
    setWorkbenchFocus(false);
  }, [chatMaximized, persistChatMaximized]);
  // Code never moves the chat: the workbench keeps the full shell (explorer at
  // its place, status bar spanning the bottom) and docks on the right, either
  // as the narrow submodule rail (chat maximized) or as the full editor panel.
  // The chat overlays the center in both cases, so restoring the editor only
  // widens the right column. Other workbench views keep their normal split.
  // The rail itself has two densities (names or icons only), remembered across
  // sessions; the chat overlay is sized against the same width.
  const [railDensity, setRailDensity] = useState<DevRailDensity>(() => readRailDensity());
  const onToggleRailDensity = useCallback(() => {
    const next: DevRailDensity = railDensity === "icons" ? "labels" : "icons";
    persistRailDensity(next);
    setRailDensity(next);
  }, [railDensity]);
  const DEV_RAIL_WIDTH = railWidthFor(railDensity);
  const DEV_PANEL_MIN_WIDTH = 240;
  const DEV_STATUS_HEIGHT = 24; // DevStatusBar is h-6
  const codeChrome = !isNarrowWorkbench && !workbenchFocus;
  const devDocked = deskView === "dev" && codeChrome;
  const chatDocked = deskView === "dev" && codeChrome;
  const devChatMaximized = devDocked && chatMaximized;
  const [devPanelWidth, setDevPanelWidth] = useState(620);
  // Live explorer width reported by DevWorkbench so the maximized chat starts
  // exactly where the center pane started (explorer stays resizable).
  const [devExplorerWidth, setDevExplorerWidth] = useState(240);
  // Measured shell width (the window minus the sidebar). The docked panel has
  // to be clamped against THIS, not window.innerWidth: three columns on a
  // 1024px window otherwise squeeze the chat down to a sliver.
  const shellMainRef = useRef<HTMLElement | null>(null);
  const [shellWidth, setShellWidth] = useState(0);
  useEffect(() => {
    const el = shellMainRef.current;
    if (!el) return;
    const update = () => setShellWidth(el.clientWidth);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  // The chat is the point of this layout: it always keeps this much room, and
  // the panel gives way (down to rail width) on a window too small for both.
  const DEV_MIN_CHAT_WIDTH = 420;
  const devPanelMax = Math.max(
    DEV_PANEL_MIN_WIDTH,
    (shellWidth || window.innerWidth)
      - devExplorerWidth
      - DEV_MIN_CHAT_WIDTH,
  );
  const devPanelEffective = Math.min(devPanelWidth, devPanelMax);
  const devRightWidth = chatDocked
    ? chatMaximized
      ? DEV_RAIL_WIDTH
      : devPanelEffective
    : 0;
  // Keep the dev chat pane within the window so the workbench stays usable:
  // always leave at least ~520px for the explorer + editor column.
  useEffect(() => {
    const clamp = () => {
      setDevChatWidth((width) =>
        Math.min(width, Math.max(280, window.innerWidth - 520)),
      );
    };
    clamp();
    window.addEventListener("resize", clamp);
    return () => window.removeEventListener("resize", clamp);
  }, []);
  const devPanelDragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const startDevPanelDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      devPanelDragRef.current = { startX: event.clientX, startWidth: devPanelEffective };
      const onMove = (ev: PointerEvent) => {
        const drag = devPanelDragRef.current;
        if (!drag) return;
        const next = drag.startWidth - (ev.clientX - drag.startX);
        setDevPanelWidth(Math.min(Math.max(DEV_PANEL_MIN_WIDTH, next), devPanelMax));
      };
      const onUp = () => {
        devPanelDragRef.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [DEV_PANEL_MIN_WIDTH, devPanelEffective, devPanelMax],
  );
  const devChatDragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const startDevChatDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      devChatDragRef.current = { startX: event.clientX, startWidth: devChatWidth };
      const onMove = (ev: PointerEvent) => {
        const drag = devChatDragRef.current;
        if (!drag) return;
        const next = drag.startWidth - (ev.clientX - drag.startX);
        setDevChatWidth(Math.min(Math.max(280, next), Math.max(320, window.innerWidth - 520)));
      };
      const onUp = () => {
        devChatDragRef.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [devChatWidth],
  );
  const effectiveRuntimeSurface =
    settingsSnapshot?.surface ?? settingsSnapshot?.runtime_surface ?? runtimeSurface;
  // Real desktop shell, or browser preview via ``?hostChrome=1`` / localStorage.
  const showHostChrome = shouldShowHostChrome(effectiveRuntimeSurface === "native");
  // Keep the app sidebar on Settings: hiding it unmounted the gear and made
  // the whole chrome jump, which reads as a permanent blink.
  const showMainSidebar = true;
  // Where "Back" from Settings / Account / Apps should land: the exact desk
  // the user left (Project, Code, …). localStorage last-route alone is too
  // easy to get wrong when the durable hash lags behind the live view.
  const currentShellRouteRef = useRef<ShellRoute>({
    view: initialRouteRef.current.view,
    activeKey: initialRouteRef.current.activeKey,
    settingsSection: initialRouteRef.current.settingsSection,
  });
  currentShellRouteRef.current = {
    view,
    activeKey,
    settingsSection: settingsInitialSection,
    tenderNotice: view === "tenders" ? tenderNotice || undefined : undefined,
    tenderPane: view === "tenders" && !tenderNotice ? tenderPane : undefined,
    careerJob: view === "career" ? careerJob || undefined : undefined,
    careerPane: view === "career" && !careerJob ? careerPane : undefined,
    leadsLead: view === "leads" ? leadsLead || undefined : undefined,
    leadsPane: view === "leads" && !leadsLead ? leadsPane : undefined,
  };

  const navigate = useCallback(
    (route: ShellRoute, options?: { replace?: boolean }) => {
      const leaving = currentShellRouteRef.current;
      if (
        isEphemeralShellView(route.view)
        && !isEphemeralShellView(leaving.view)
      ) {
        overlayReturnRouteRef.current = { ...leaving };
      }
      setActiveKey(route.activeKey);
      setView(route.view);
      setSettingsInitialSection(route.settingsSection);
      setTenderNotice(route.view === "tenders" ? route.tenderNotice || "" : "");
      setTenderPane(
        route.view === "tenders"
          ? route.tenderNotice || route.tenderPane === "tenders"
            ? "tenders"
            : "home"
          : "home",
      );
      setCareerJob(route.view === "career" ? route.careerJob || "" : "");
      setCareerPane(
        route.view === "career"
          ? route.careerJob || route.careerPane === "offers"
            ? "offers"
            : "home"
          : "home",
      );
      setLeadsLead(route.view === "leads" ? route.leadsLead || "" : "");
      setLeadsPane(
        route.view === "leads"
          ? route.leadsLead || route.leadsPane === "book"
            ? "book"
            : "home"
          : "home",
      );
      writeShellRoute(route, options?.replace);
      // Remember which product module owns this chat so a later sidebar click
      // reopens Code / Montage / … instead of dumping into plain chat.
      const module = normalizeChatModuleView(route.view);
      if (route.activeKey && module && module !== "chat") {
        void updateSidebarState((current) => {
          if (current.module_by_key[route.activeKey!] === module) return current;
          return {
            ...current,
            module_by_key: {
              ...current.module_by_key,
              [route.activeKey!]: module,
            },
          };
        });
      }
    },
    [updateSidebarState],
  );

  // The checkpoint CTA and "View plan" need the Code module on screen
  // before DevWorkbench can open Git > Checkpoints or the session-plan tab;
  // the workbench consumes the pending request on mount, this only brings it up.
  useEffect(() => {
    const openCode = () => {
      if (currentShellRouteRef.current.view === "dev") return;
      navigate({
        view: "dev",
        activeKey: currentShellRouteRef.current.activeKey,
        settingsSection: "overview",
      });
    };
    window.addEventListener(OPEN_CHECKPOINTS_EVENT, openCode);
    window.addEventListener(OPEN_SESSION_PLAN_EVENT, openCode);
    window.addEventListener(OPEN_BOARD_TASK_EVENT, openCode);
    return () => {
      window.removeEventListener(OPEN_CHECKPOINTS_EVENT, openCode);
      window.removeEventListener(OPEN_SESSION_PLAN_EVENT, openCode);
      window.removeEventListener(OPEN_BOARD_TASK_EVENT, openCode);
    };
  }, [navigate]);

  // CLI / desktop `?project=` is handled after openProjectWorkspace is defined
  // (resume last chat for that folder; create only when none exists).

  useEffect(() => {
    const applyRoute = () => {
      const route = readShellRoute();
      setActiveKey(route.activeKey);
      setView(route.view);
      setSettingsInitialSection(route.settingsSection);
      setTenderNotice(route.view === "tenders" ? route.tenderNotice || "" : "");
      setTenderPane(
        route.view === "tenders"
          ? route.tenderNotice || route.tenderPane === "tenders"
            ? "tenders"
            : "home"
          : "home",
      );
      setCareerJob(route.view === "career" ? route.careerJob || "" : "");
      setCareerPane(
        route.view === "career"
          ? route.careerJob || route.careerPane === "offers"
            ? "offers"
            : "home"
          : "home",
      );
      setLeadsLead(route.view === "leads" ? route.leadsLead || "" : "");
      setLeadsPane(
        route.view === "leads"
          ? route.leadsLead || route.leadsPane === "book"
            ? "book"
            : "home"
          : "home",
      );
      setWorkspaceError(null);
      if (route.view === "chat" && !route.activeKey) {
        setDraftWorkspaceScope(null);
      }
      if (route.openProjectPanel) {
        setDevProjectHomeRequest((current) => ({
          nonce: (current?.nonce ?? 0) + 1,
        }));
      }
      if (route.openTemplatesPanel) {
        setDevTemplatesRequest((current) => ({
          nonce: (current?.nonce ?? 0) + 1,
        }));
      }
      if (route.openEvolvePanel) {
        setDevEvolveRequest((current) => ({
          nonce: (current?.nonce ?? 0) + 1,
        }));
      }
    };
    window.addEventListener("hashchange", applyRoute);
    return () => window.removeEventListener("hashchange", applyRoute);
  }, []);

  // Cold boot on `#/project` / `#/code?panel=project`.
  useEffect(() => {
    if (initialRouteRef.current?.openProjectPanel) {
      setDevProjectHomeRequest((current) => ({
        nonce: (current?.nonce ?? 0) + 1,
      }));
    }
    if (initialRouteRef.current?.openTemplatesPanel) {
      setDevTemplatesRequest((current) => ({
        nonce: (current?.nonce ?? 0) + 1,
      }));
    }
    if (initialRouteRef.current?.openEvolvePanel) {
      setDevEvolveRequest((current) => ({
        nonce: (current?.nonce ?? 0) + 1,
      }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchSettings(token)
      .then((payload) => {
        if (!cancelled) setSettingsSnapshot(payload);
      })
      .catch(() => {
        if (!cancelled) setSettingsSnapshot(null);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Targeted IDE sync after connect / logout / plan change / OpenRouter Free.
  // Not a systematic settings poll: only when the account identity actually moves.
  const refreshWorkspaceState = useCallback(() => {
    void Promise.all([
      reloadAccount({ refresh: true }),
      fetchSettings(token, "", { syncCatalog: true })
        .then(setSettingsSnapshot)
        .catch(() => {}),
    ]);
  }, [reloadAccount, token]);

  useEffect(() => {
    if (!account) return;
    const key = [
      account.connected ? "1" : "0",
      account.plan || "",
      account.managed_key_active ? "1" : "0",
      account.status || "",
    ].join("|");
    const prev = accountSyncKeyRef.current;
    accountSyncKeyRef.current = key;
    // Skip the first observation (initial load already fetched settings).
    if (prev === null || prev === key) return;
    void fetchSettings(token, "", { syncCatalog: true })
      .then(setSettingsSnapshot)
      .catch(() => {});
  }, [account, token]);

  useEffect(() => {
    if (
      !settingsSnapshot?.updates?.enabled ||
      !settingsSnapshot.updates.autoCheck ||
      !settingsSnapshot.updates.configured
    ) {
      return;
    }
    let cancelled = false;
    let hiddenAt = document.visibilityState === "hidden" ? Date.now() : 0;
    const applyInfo = (info: UpdateInfo) => {
      if (cancelled) return;
      // Kept even when it cannot be installed in place (a .deb, an .rpm):
      // the card then says where the update comes from instead of
      // offering a button, which beats staying silent about a version
      // the user can perfectly well install by hand.
      setAvailableUpdate((current) =>
        nextAvailableUpdate(current, info, readDismissedUpdateToast()),
      );
    };
    const check = (force: boolean = false) => {
      void checkVersion(tokenRef.current, force)
        .then(applyInfo)
        .catch(() => {
          // Automatic checks are deliberately silent; Settings exposes errors on demand.
        });
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        hiddenAt = Date.now();
        return;
      }
      const awayMs = hiddenAt ? Date.now() - hiddenAt : 0;
      hiddenAt = 0;
      // Tauri/WebView fires focus every few seconds. That must not re-open Later.
      if (awayMs >= UPDATE_RECHECK_AFTER_HIDDEN_MS) check(false);
    };

    check();
    const interval = window.setInterval(() => check(true), 6 * 60 * 60 * 1_000);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [
    settingsSnapshot?.updates?.autoCheck,
    settingsSnapshot?.updates?.configured,
    settingsSnapshot?.updates?.enabled,
  ]);

  const openProductUrl = useCallback(async (url: string) => {
    // Desktop WebView swallows target=_blank / window.open. The opener plugin
    // (macOS, Windows) or the gateway xdg-open route (Linux) is the real path.
    const result = await openExternalUrl(token, url);
    if (!result.opened) window.open(url, "_blank", "noopener,noreferrer");
  }, [token]);

  const installAvailableUpdate = useCallback(async (fallbackUrl?: string) => {
    if (updateInFlight.current) return;
    updateInFlight.current = true;
    setUpdateBusy(true);
    setUpdateError(null);
    setUpdateStatus({ state: "downloading", progress: 0, downloadedBytes: 0, totalBytes: 0 });
    try {
      await downloadAndInstallUpdate(token, setUpdateStatus);
    } catch (error) {
      updateInFlight.current = false;
      setUpdateBusy(false);
      const message = error instanceof Error ? error.message : String(error);
      setUpdateError(message);
      setUpdateStatus(null);
      notify({
        level: "error", source: "update",
        title: t("updates.installFailed", { defaultValue: "Install failed: {{error}}", error: message }),
        key: "update:install-error",
      });
      if (fallbackUrl) {
        try {
          await openProductUrl(fallbackUrl);
        } catch {
          // The card now names the failure. Do not hide it behind Settings.
        }
      }
    }
  }, [notify, openProductUrl, t, token]);
  installAvailableUpdateRef.current = () => installAvailableUpdate();

  const handleAnnouncementOpen = useCallback(
    async (item: { id: string; url?: string; kind?: string }) => {
      const url = item.url?.trim();
      const release = item.kind === "release" || item.id.startsWith("release-");
      if (!release) {
        if (url) await openProductUrl(url);
        return;
      }
      // Same promise as the Install Now card: download + replace + restart.
      // If this build cannot update itself, the download page still opens.
      if (availableUpdate?.available && availableUpdate.supported !== false) {
        await installAvailableUpdate(url);
        return;
      }
      setUpdateBusy(true);
      setUpdateError(null);
      try {
        const info = await checkVersion(token, true);
        setAvailableUpdate((current) =>
          nextAvailableUpdate(current, info, readDismissedUpdateToast()),
        );
        if (info.available && info.supported !== false) {
          await installAvailableUpdate(url);
          return;
        }
      } catch (error) {
        setUpdateError(error instanceof Error ? error.message : String(error));
      }
      setUpdateBusy(false);
      if (url) await openProductUrl(url);
    },
    [availableUpdate, installAvailableUpdate, openProductUrl, token],
  );

  const skipAvailableUpdate = useCallback(async () => {
    const version = availableUpdate?.latestVersion;
    if (version) dismissUpdateToast(version);
    setUpdateError(null);
    setAvailableUpdate(null);
    if (!version) return;
    try {
      await updatePreferences(token, { skippedVersion: version });
    } catch {
      // The banner remains dismissed for this session even if persistence fails.
    }
  }, [availableUpdate?.latestVersion, token]);

  const dismissAvailableUpdate = useCallback(() => {
    const version = availableUpdate?.latestVersion;
    if (version) dismissUpdateToast(version);
    setAvailableUpdate(null);
    setUpdateError(null);
  }, [availableUpdate?.latestVersion]);

  // Non-subscribed / BYOK users must configure a provider before chat works.
  // Managed-key subscribers get OpenRouter from the account; no banner.
  // Keep showing until a provider is configured (dismiss removed on purpose).
  const providerSetupNeeded = useMemo(() => {
    if (!settingsSnapshot) return false;
    if (account?.connected && account?.managed_key_active) return false;
    const providers = settingsSnapshot.providers ?? [];
    return !providers.some((provider) => provider.configured);
  }, [account?.connected, account?.managed_key_active, settingsSnapshot]);

  // First-run wizard is browser-local by default; after an upgrade / cleared
  // storage it would reappear even when providers are already configured.
  // Hydrate from ~/.navin and auto-complete when the install is ready.
  useEffect(() => {
    let cancelled = false;
    void hydrateOnboardingFromServer(token).then((state) => {
      if (cancelled) return;
      if (state.completed) setShowFirstRunWizard(false);
    });
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (!showFirstRunWizard) return;
    if (wizardFreeStage) return;
    if (!settingsSnapshot) return;
    const ready =
      (settingsSnapshot.providers ?? []).some((provider) => provider.configured)
      || Boolean(account?.connected);
    if (!ready) return;
    markOnboardingComplete({ path: "skip" }, token);
    setShowFirstRunWizard(false);
  }, [account?.connected, settingsSnapshot, showFirstRunWizard, token, wizardFreeStage]);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        SIDEBAR_STORAGE_KEY,
        hostSidebarOpen ? "1" : "0",
      );
    } catch {
      // ignore storage errors (private mode, etc.)
    }
  }, [hostSidebarOpen]);

  useEffect(() => {
    writeSessionUpdateChatIds(updatedChatIds);
  }, [updatedChatIds]);

  const refreshPairingRequests = useCallback(async () => {
    try {
      const payload = await fetchPairingRequests(token);
      const requests = Array.isArray(payload.requests) ? payload.requests : [];
      setPairingRequests(requests);
      setSnoozedPairingCodes((current) => {
        if (current.size === 0) return current;
        const activeCodes = new Set(requests.map((request) => request.code));
        const now = Date.now();
        const next = new Map(
          Array.from(current).filter(
            ([code, snoozedUntil]) => activeCodes.has(code) && snoozedUntil > now,
          ),
        );
        return next.size === current.size ? current : next;
      });
    } catch {
      // Pairing is an opportunistic WebUI affordance. The slash command path
      // remains available if this polling request fails.
    }
  }, [token]);

  useEffect(() => {
    void refreshPairingRequests();
    const timer = window.setInterval(() => {
      void refreshPairingRequests();
    }, PAIRING_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refreshPairingRequests]);

  const activeSession = useMemo<ChatSummary | null>(() => {
    if (!activeKey) return null;
    return sessions.find((s) => s.key === activeKey) ?? null;
  }, [sessions, activeKey]);

  const runningChatIdList = useMemo(() => Array.from(runningChatIds), [runningChatIds]);
  const updatedChatIdList = useMemo(() => Array.from(updatedChatIds), [updatedChatIds]);
  const activeChatId = activeSession?.chatId ?? null;
  useEffect(() => {
    activeChatIdRef.current = activeChatId;
    if (!activeChatId) return;
    setUpdatedChatIds((current) => {
      if (!current.has(activeChatId)) return current;
      const next = new Set(current);
      next.delete(activeChatId);
      return next;
    });
  }, [activeChatId]);
  const activeWorkspaceScope = useMemo<WorkspaceScopePayload | null>(() => {
    if (activeChatId && workspaceOverrides[activeChatId]) {
      return workspaceOverrides[activeChatId];
    }
    if (activeSession?.workspaceScope) {
      return activeSession.workspaceScope;
    }
    return draftWorkspaceScope ?? workspaces?.default_scope ?? null;
  }, [
    activeChatId,
    activeSession?.workspaceScope,
    draftWorkspaceScope,
    workspaceOverrides,
    workspaces?.default_scope,
  ]);
  const activeChatRunning = activeChatId ? runningChatIds.has(activeChatId) : false;

  // Remember the Code desk (folder + last chat) so leaving to #/new or a
  // Studio module and clicking Code again restores the same project + chat.
  useEffect(() => {
    if (view !== "dev") return;
    const path = activeWorkspaceScope?.project_path?.trim() || null;
    const defaultPath = workspaces?.default_scope?.project_path;
    const realProject =
      path &&
      !isNavinInternalPath(path) &&
      !sameWorkspacePath(path, defaultPath)
        ? path
        : null;
    if (!activeKey && !realProject) return;
    rememberLastDevContext({
      ...(activeKey ? { chatKey: activeKey } : {}),
      ...(realProject
        ? {
            projectPath: realProject,
            projectName: activeWorkspaceScope?.project_name ?? null,
          }
        : {}),
    });
  }, [
    activeKey,
    activeWorkspaceScope?.project_name,
    activeWorkspaceScope?.project_path,
    view,
    workspaces?.default_scope?.project_path,
  ]);

  // Restore the last opened project when entering a workbench view, so the
  // selected project survives page reloads.
  //
  // The scope is never actually null here: it falls back to default_scope,
  // navin's internal ~/.navin/workspace. So the condition to restore is "no
  // real project is open", which selectedProjectScope answers by returning
  // null for the internal workspace. Restoring runs at most once, leaving a
  // user who deliberately opens the internal workspace where they asked to be.
  const restoredLastProjectRef = useRef(false);
  useEffect(() => {
    if (!isWorkbenchView(view) || activeChatId || pendingCliProjectPath) return;
    if (restoredLastProjectRef.current) return;
    const base = workspaces?.default_scope;
    if (!base) return;
    if (selectedProjectScope(activeWorkspaceScope, base)) return;
    const last = sidebarState.recent_projects?.find(
      (entry) => !sameWorkspacePath(entry.path, base.project_path),
    );
    if (!last) return;
    restoredLastProjectRef.current = true;
    setDraftWorkspaceScope(
      normalizeWorkspaceScope({
        ...base,
        project_path: last.path,
        project_name: last.name || projectNameFromPath(last.path),
        restrict_to_workspace: base.access_mode === "restricted",
      }),
    );
  }, [
    activeChatId,
    activeWorkspaceScope,
    pendingCliProjectPath,
    sidebarState.recent_projects,
    view,
    workspaces?.default_scope,
  ]);

  // Tag the folder with the module it is being worked in, so picking that
  // folder again on the new-chat screen reopens Code / Montage / … instead of
  // landing in plain chat and losing the workbench.
  useEffect(() => {
    const module = normalizeChatModuleView(view);
    if (!module || module === "chat") return;
    // Wait for the default scope: tagging before it loads would brand the
    // default workspace itself, and then every new chat would jump into Code.
    const defaultPath = workspaces?.default_scope?.project_path;
    if (!defaultPath) return;
    const key = projectModuleKey(activeWorkspaceScope?.project_path);
    if (!key || isNavinInternalPath(key)) return;
    if (sameWorkspacePath(key, defaultPath)) return;
    if (sidebarState.module_by_path?.[key] === module) return;
    void updateSidebarState((current) => {
      if (current.module_by_path?.[key] === module) return current;
      return {
        ...current,
        module_by_path: { ...current.module_by_path, [key]: module },
      };
    });
  }, [
    activeWorkspaceScope?.project_path,
    sidebarState.module_by_path,
    updateSidebarState,
    view,
    workspaces?.default_scope?.project_path,
  ]);

  // Without an active chat the workbench reads its file tree through the
  // shared "websocket:webui-dev" session key. That scope only existed
  // client-side, so on startup the gateway kept serving its internal
  // workspace (the AppData folders) until the first message persisted the
  // real project. Persisting the draft scope under the shared key makes the
  // explorer show the restored project immediately.
  const lastPushedDevScopeRef = useRef<string | null>(null);
  useEffect(() => {
    if (activeChatId) return;
    const path = draftWorkspaceScope?.project_path?.trim();
    if (!path || !draftWorkspaceScope) return;
    if (sameWorkspacePath(path, workspaces?.default_scope?.project_path)) return;
    if (lastPushedDevScopeRef.current === path) return;
    lastPushedDevScopeRef.current = path;
    client.setWorkspaceScope("webui-dev", draftWorkspaceScope);
  }, [activeChatId, client, draftWorkspaceScope, workspaces?.default_scope?.project_path]);

  const refreshWorkspaces = useCallback(async () => {
    try {
      const payload = await fetchWorkspaces(token);
      setWorkspaces(payload);
    } catch {
      setWorkspaces(null);
    }
  }, [token]);

  useEffect(() => {
    void refreshWorkspaces();
  }, [refreshWorkspaces]);

  useEffect(() => {
    if (loading) return;
    const knownChatIds = new Set(sessions.map((session) => session.chatId));
    setUpdatedChatIds((current) => {
      const next = new Set(
        Array.from(current).filter((chatId) => knownChatIds.has(chatId)),
      );
      return next.size === current.size ? current : next;
    });
    setWorkspaceOverrides((current) => {
      const entries = Object.entries(current).filter(([chatId]) => knownChatIds.has(chatId));
      return entries.length === Object.keys(current).length ? current : Object.fromEntries(entries);
    });
  }, [loading, sessions]);

  // A chat that is gone (deleted, or not listed yet) only clears the selection.
  // Falling back to the default route sent Tchat into Code, which is what made
  // "New chat" look like it jumped to the editor and then froze there.
  useEffect(() => {
    if (loading || !activeKey) return;
    if (sessions.some((session) => session.key === activeKey)) return;
    const currentRoute = readShellRoute();
    navigate({ ...currentRoute, activeKey: null }, { replace: true });
  }, [activeKey, loading, navigate, sessions]);

  // Unstick Code when the open chat is clearly Ask (`/ask` still on
  // `#/code?chat=…` after an older tag). Once per chat: a sessions refresh
  // used to re-run this and remount the shell on every poll.
  const unstuckAskKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (loading || !activeKey || view !== "dev") return;
    if (unstuckAskKeyRef.current === activeKey) return;
    const selected = sessions.find((session) => session.key === activeKey);
    if (!selected) return;
    if (inferChatModuleFromPreview(selected.preview) !== "chat") return;
    unstuckAskKeyRef.current = activeKey;
    writeComposerTurnMode("ask");
    if (sidebarState.module_by_key[activeKey]) {
      void updateSidebarState((current) => {
        if (!(activeKey in current.module_by_key)) return current;
        const module_by_key = { ...current.module_by_key };
        delete module_by_key[activeKey];
        return { ...current, module_by_key };
      });
    }
    navigate(
      {
        view: "chat",
        activeKey,
        settingsSection: "overview",
      },
      { replace: true },
    );
  }, [
    activeKey,
    loading,
    navigate,
    sessions,
    sidebarState.module_by_key,
    updateSidebarState,
    view,
  ]);

  useEffect(() => {
    return client.onSessionUpdate((chatId, scope, workspaceScope) => {
      if (scope === "thread") {
        setUpdatedChatIds((current) => {
          const next = new Set(current);
          if (activeChatIdRef.current === chatId) {
            next.delete(chatId);
          } else {
            next.add(chatId);
          }
          return next.size === current.size && next.has(chatId) === current.has(chatId)
            ? current
            : next;
        });
      }
      if (!workspaceScope) return;
      const next = normalizeWorkspaceScope(workspaceScope);
      setWorkspaceOverrides((current) => ({
        ...current,
        [chatId]: next,
      }));
      setDraftWorkspaceScope(next);
      setWorkspaceError(null);
      void refreshWorkspaces();
    });
  }, [client, refreshWorkspaces]);

  useEffect(() => {
    return client.onError((error) => {
      if (error.kind !== "workspace_scope_rejected") return;
      setWorkspaceError(t("errors.workspaceScopeRejected.body"));
      void refreshWorkspaces();
    });
  }, [client, refreshWorkspaces, t]);

  useEffect(() => {
    if (loading) return;
    const activeRunIds = sessions
      .filter((session) => typeof session.runStartedAt === "number")
      .map((session) => session.chatId);
    if (activeRunIds.length === 0) return;

    for (const chatId of activeRunIds) {
      client.attach(chatId);
    }
    setRunningChatIds((current) => {
      let changed = false;
      const next = new Set(current);
      for (const chatId of activeRunIds) {
        if (!next.has(chatId)) changed = true;
        next.add(chatId);
      }
      if (!changed) return current;
      runningChatIdsRef.current = next;
      return next;
    });
    setUpdatedChatIds((current) => {
      let changed = false;
      const next = new Set(current);
      for (const chatId of activeRunIds) {
        if (next.delete(chatId)) changed = true;
      }
      return changed ? next : current;
    });
  }, [client, loading, sessions]);

  const closeHostSidebar = useCallback(() => {
    setHostSidebarOpen(false);
  }, []);

  const openHostSidebar = useCallback(() => {
    setHostSidebarOpen(true);
  }, []);

  const closeMobileSidebar = useCallback(() => {
    setMobileSidebarOpen(false);
  }, []);

  const toggleSidebar = useCallback(() => {
    const isNativeHost =
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 1024px)").matches;
    if (isNativeHost) {
      setHostSidebarOpen((v) => !v);
    } else {
      setMobileSidebarOpen((v) => !v);
    }
  }, []);

  const applyWorkspaceScope = useCallback(
    (scope: WorkspaceScopePayload) => {
      const next = normalizeWorkspaceScope(scope);
      setWorkspaceError(null);
      if (activeChatId) {
        if (!activeChatRunning) {
          client.setWorkspaceScope(activeChatId, next);
        }
        return;
      }
      setDraftWorkspaceScope(next);
    },
    [activeChatId, activeChatRunning, client],
  );

  // Composer variant: also record the chosen project in the shared
  // recent-projects list so it shows up in every project selector.
  const applyWorkspaceScopeFromComposer = useCallback(
    (scope: WorkspaceScopePayload) => {
      applyWorkspaceScope(scope);
      const path = scope.project_path?.trim();
      if (!path || path === workspaces?.default_scope?.project_path) return;
      const name = scope.project_name || projectNameFromPath(path);
      void updateSidebarState((current) => {
        const rest = (current.recent_projects ?? []).filter(
          (entry) => entry.path !== path,
        );
        return {
          ...current,
          recent_projects: [{ path, name }, ...rest].slice(0, 20),
        };
      });
      // A folder last used in Code / Montage reopens that desk. Untagged
      // folders stay in Tchat, so New chat itself never jumps to the editor.
      const tagged = resolveProjectOpenView(path, sidebarState.module_by_path, "chat");
      if (tagged !== "chat" && tagged !== view) {
        navigate({ ...readShellRoute(), view: tagged as ShellView });
      }
    },
    [
      applyWorkspaceScope,
      navigate,
      sidebarState.module_by_path,
      updateSidebarState,
      view,
      workspaces?.default_scope?.project_path,
    ],
  );

  const [devOpenFileRequest, setDevOpenFileRequest] = useState<
    { path: string; nonce: number; mode?: "preview" | "code" | "diff" } | null
  >(null);
  const [devProjectHomeRequest, setDevProjectHomeRequest] = useState<{
    nonce: number;
  } | null>(null);
  const [devTemplatesRequest, setDevTemplatesRequest] = useState<{
    nonce: number;
  } | null>(null);
  const [devEvolveRequest, setDevEvolveRequest] = useState<{
    nonce: number;
  } | null>(null);
  // Dropped as soon as Code has acted on them. They are held here only to
  // survive the gap before the workbench is mounted; keeping them afterwards
  // makes them replay on remount, reopening tabs the user has closed.
  const clearDevOpenFileRequest = useCallback(() => setDevOpenFileRequest(null), []);
  const clearDevProjectHomeRequest = useCallback(() => setDevProjectHomeRequest(null), []);
  const clearDevTemplatesRequest = useCallback(() => setDevTemplatesRequest(null), []);
  const clearDevEvolveRequest = useCallback(() => setDevEvolveRequest(null), []);

  // Agent / chat file previews always land in the left Code workbench
  // (all modules), and the chat pane on the right stays open.
  const onOpenFileInDevEditor = useCallback(
    (path: string, options?: { mode?: "preview" | "code" | "diff" }) => {
      if (view !== "dev") {
        navigate({
          view: "dev",
          activeKey,
          settingsSection: "overview",
        });
      }
      setDevOpenFileRequest((current) => ({
        path,
        nonce: (current?.nonce ?? 0) + 1,
        mode: options?.mode ?? "code",
      }));
    },
    [activeKey, navigate, view],
  );

  // Agent open_preview: always land on Code and open Preview / Mobile, even
  // if the user is still on Chat (DevWorkbench is otherwise unmounted).
  const [devPreviewOpenRequest, setDevPreviewOpenRequest] = useState<{
    kind: "web" | "mobile";
    url?: string;
    nonce: number;
  } | null>(null);
  useEffect(() => {
    return client.onPreviewOpenRequest((request) => {
      const nextKey = request.chatId
        ? request.chatId.startsWith("websocket:")
          ? request.chatId
          : `websocket:${request.chatId}`
        : activeKey;
      navigate({
        view: "dev",
        activeKey: nextKey,
        settingsSection: "overview",
      });
      setDevPreviewOpenRequest((current) => ({
        kind: request.kind,
        ...(request.url ? { url: request.url } : {}),
        nonce: (current?.nonce ?? 0) + 1,
      }));
    });
  }, [activeKey, client, navigate]);
  const clearDevPreviewOpenRequest = useCallback(() => setDevPreviewOpenRequest(null), []);

  // A coding guess from the gateway used to yank Tchat onto #/code. New chat
  // is Tchat: the user opens Code when they want the editor, not when a verb
  // in the first sentence looked like software.
  useEffect(() => {
    return client.onProductModuleRequest((request) => {
      const nextView = VIEW_BY_PRODUCT_MODULE[request.module];
      if (!nextView) return;
      // Already on a workbench: do not fight the view they opened.
      if (isWorkbenchView(view)) return;
      // Tchat / #/new stays Tchat. No automatic jump to Code.
      if (view === "chat") return;
      const nextKey = request.chatId
        ? request.chatId.startsWith("websocket:")
          ? request.chatId
          : `websocket:${request.chatId}`
        : activeKey;
      navigate({ view: nextView, activeKey: nextKey, settingsSection: "overview" });
    });
  }, [activeKey, client, navigate, view]);

  const [devAutoSendRequest, setDevAutoSendRequest] = useState<{
    text: string;
    nonce: number;
    documentTemplate?: { category: string; name: string; title?: string };
  } | null>(null);
  const [chatAutoSendRequest, setChatAutoSendRequest] = useState<{
    text: string;
    nonce: number;
  } | null>(null);
  const onRunDevAction = useCallback(
    (
      text: string,
      documentTemplate?: { category: string; name: string; title?: string },
    ) => {
      setDevAutoSendRequest((current) => ({
        text,
        documentTemplate,
        nonce: (current?.nonce ?? 0) + 1,
      }));
    },
    [],
  );

  const [devComposerSeed, setDevComposerSeed] = useState<{
    text: string;
    nonce: number;
    files?: ProjectFileMatch[];
    replace?: boolean;
    ensureCommand?: string;
    mediaTemplate?: { id: string; title?: string; kind?: string; format?: string };
    mediaTemplates?: { id: string; title?: string; kind?: string; format?: string }[];
    localFiles?: File[];
  } | null>(null);
  // Monotonic across the whole app lifetime: the state resets to null after
  // consumption, so deriving the nonce from it would restart at 1 - a value
  // the composer's consumed-nonce ref already holds, silently swallowing the
  // next seed (first upload from the media library did nothing).
  const devComposerSeedNonceRef = useRef(0);
  const onSeedDevComposer = useCallback((
    text: string,
    files?: ProjectFileMatch[],
    options?: {
      replace?: boolean;
      ensureCommand?: string;
      mediaTemplate?: { id: string; title?: string; kind?: string; format?: string };
      mediaTemplates?: { id: string; title?: string; kind?: string; format?: string }[];
      localFiles?: File[];
    },
  ) => {
    devComposerSeedNonceRef.current += 1;
    setDevComposerSeed({
      text,
      files,
      replace: options?.replace,
      ensureCommand: options?.ensureCommand,
      mediaTemplate: options?.mediaTemplate,
      mediaTemplates: options?.mediaTemplates,
      localFiles: options?.localFiles,
      nonce: devComposerSeedNonceRef.current,
    });
  }, []);
  // Once the composer landed the seed, drop it: keeping it in state means a
  // composer remount (sending from an empty chat swaps the hero composer for
  // the thread one) would re-apply the already-sent text into the input.
  const onDevComposerSeedConsumed = useCallback((nonce: number) => {
    setDevComposerSeed((current) =>
      current && current.nonce === nonce ? null : current,
    );
  }, []);

  const rememberRecentProject = useCallback(
    (path: string, name: string) => {
      void updateSidebarState((current) => {
        const rest = (current.recent_projects ?? []).filter(
          (entry) => entry.path !== path,
        );
        return {
          ...current,
          recent_projects: [{ path, name }, ...rest].slice(0, 20),
        };
      });
    },
    [updateSidebarState],
  );

  const openingProjectRef = useRef<Map<string, Promise<string | null>>>(new Map());
  const creatingChatRef = useRef<Promise<string | null> | null>(null);

  const onCreateChat = useCallback(async (
    workspaceScope?: WorkspaceScopePayload | null,
    options?: { keepWorkbench?: boolean },
  ) => {
    if (creatingChatRef.current) return creatingChatRef.current;
    const run = (async () => {
      try {
        const scope = workspaceScope ?? activeWorkspaceScope;
        const projectPath =
          scope?.project_path ?? workspaces?.default_scope?.project_path ?? null;
        const reusable = findReusableEmptyChat(sessions, activeKey, projectPath);
        const nextView = viewForCreatedChat(view, {
          keepWorkbench: options?.keepWorkbench,
          isWorkbench: isWorkbenchView(view),
        }) as ShellView;
        if (reusable) {
          navigate({
            view: nextView,
            activeKey: reusable.key,
            settingsSection: "overview",
          });
          setMobileSidebarOpen(false);
          if (scope) {
            setWorkspaceOverrides((current) => ({
              ...current,
              [reusable.chatId]: normalizeWorkspaceScope(scope),
            }));
          }
          return reusable.chatId;
        }
        const chatId = await createChat(scope);
        navigate({
          view: nextView,
          activeKey: `websocket:${chatId}`,
          settingsSection: "overview",
        });
        setMobileSidebarOpen(false);
        if (scope) {
          setWorkspaceOverrides((current) => ({
            ...current,
            [chatId]: normalizeWorkspaceScope(scope),
          }));
        }
        return chatId;
      } catch (e) {
        console.error("Failed to create chat", e);
        if (e instanceof Error && e.message.startsWith("workspace_scope_rejected:")) {
          setWorkspaceError(t("errors.workspaceScopeRejected.body"));
        }
        return null;
      }
    })();
    creatingChatRef.current = run;
    try {
      return await run;
    } finally {
      creatingChatRef.current = null;
    }
  }, [
    activeKey,
    activeWorkspaceScope,
    createChat,
    navigate,
    sessions,
    t,
    view,
    workspaces?.default_scope?.project_path,
  ]);

  /**
   * Open a project folder in Code (or the current workbench view) in one click:
   * reuse an existing chat for that project when possible - never create a
   * duplicate just to switch folders, and never rebind the wrong active chat.
   */
  const openProjectWorkspace = useCallback(
    async (
      projectPath: string,
      projectName: string | undefined,
      targetView: ShellView,
      options?: { replace?: boolean },
    ) => {
      const base = activeWorkspaceScope ?? workspaces?.default_scope;
      const trimmed = projectPath.trim();
      if (!base || !trimmed) return null;
      const pathKey = normalizeWorkspacePath(trimmed);
      const inflight = openingProjectRef.current.get(pathKey);
      if (inflight) return inflight;

      const run = (async (): Promise<string | null> => {
        const name = projectName?.trim() || projectNameFromPath(trimmed);
        const nextScope = normalizeWorkspaceScope({
          ...base,
          project_path: trimmed,
          project_name: name,
          restrict_to_workspace: base.access_mode === "restricted",
        });
        rememberRecentProject(trimmed, name);

        const existing = findSessionForProject(sessions, trimmed);
        if (existing) {
          navigate(
            {
              view: targetView,
              activeKey: existing.key,
              settingsSection: "overview",
            },
            { replace: options?.replace },
          );
          return existing.chatId;
        }

        try {
          const chatId = await createChat(nextScope);
          setWorkspaceOverrides((current) => ({
            ...current,
            [chatId]: nextScope,
          }));
          navigate(
            {
              view: targetView,
              activeKey: `websocket:${chatId}`,
              settingsSection: "overview",
            },
            { replace: options?.replace },
          );
          return chatId;
        } catch (e) {
          console.error("Failed to open project workspace", e);
          if (e instanceof Error && e.message.startsWith("workspace_scope_rejected:")) {
            setWorkspaceError(t("errors.workspaceScopeRejected.body"));
          }
          return null;
        }
      })();

      openingProjectRef.current.set(pathKey, run);
      try {
        return await run;
      } finally {
        openingProjectRef.current.delete(pathKey);
      }
    },
    [
      activeWorkspaceScope,
      createChat,
      navigate,
      rememberRecentProject,
      sessions,
      t,
      workspaces?.default_scope,
    ],
  );

  // `navin .` / desktop open-folder: land in Code on the last chat for that
  // project. Create only when the sessions list is loaded and empty for it.
  useEffect(() => {
    if (!pendingCliProjectPath) return;
    if (!workspaces?.default_scope) return;
    if (loading) return;
    const projectPath = pendingCliProjectPath;
    setPendingCliProjectPath("");
    setWorkspaceError(null);
    void openProjectWorkspace(
      projectPath,
      projectNameFromPath(projectPath),
      "dev",
      { replace: true },
    );
  }, [loading, openProjectWorkspace, pendingCliProjectPath, workspaces?.default_scope]);

  const onSelectDevProject = useCallback(
    (projectPath: string, projectName?: string) => {
      const target = isWorkbenchView(view) ? view : "dev";
      void openProjectWorkspace(projectPath, projectName, target);
    },
    [openProjectWorkspace, view],
  );

  /**
   * CRM and Team store data on `/api/sessions/:key/...`. Opening those desks
   * without a chat must reuse the last one (or create one for the last folder)
   * instead of rendering a blank "pick a chat" center.
   */
  const ensureChatInFlightRef = useRef(false);
  const ensureActiveChat = useCallback(
    (targetView: ShellView) => {
      const attach = (key: string) => {
        if (view === targetView && activeKey === key) return;
        navigate({
          view: targetView,
          activeKey: key,
          settingsSection: "overview",
        });
      };

      if (activeKey && sessions.some((session) => session.key === activeKey)) {
        attach(activeKey);
        return;
      }

      const last = readLastDevContext();
      if (last?.chatKey && sessions.some((session) => session.key === last.chatKey)) {
        attach(last.chatKey);
        return;
      }

      const lastProject = last?.projectPath?.trim() || null;
      if (lastProject) {
        const bound = findSessionForProject(sessions, lastProject);
        if (bound?.key) {
          attach(bound.key);
          return;
        }
      }

      const latest = sessions
        .filter(isListedSidebarSession)
        .sort((a, b) => {
          const aTs = Date.parse(a.updatedAt ?? a.createdAt ?? "") || 0;
          const bTs = Date.parse(b.updatedAt ?? b.createdAt ?? "") || 0;
          return bTs - aTs;
        })[0];
      if (latest?.key) {
        attach(latest.key);
        return;
      }

      const defaultPath = workspaces?.default_scope?.project_path;
      const projectPath =
        lastProject ||
        sidebarState.recent_projects?.find(
          (entry) =>
            !sameWorkspacePath(entry.path, defaultPath) && !isNavinInternalPath(entry.path),
        )?.path ||
        null;

      if (projectPath && !isNavinInternalPath(projectPath)) {
        const name =
          last?.projectName ||
          sidebarState.recent_projects?.find((entry) =>
            sameWorkspacePath(entry.path, projectPath),
          )?.name;
        void openProjectWorkspace(projectPath, name, targetView);
        return;
      }

      if (view !== targetView) {
        navigate({
          view: targetView,
          activeKey: null,
          settingsSection: "overview",
        });
      }

      if (ensureChatInFlightRef.current) return;
      ensureChatInFlightRef.current = true;
      void (async () => {
        try {
          const scope = activeWorkspaceScope ?? workspaces?.default_scope;
          const chatId = await createChat(scope);
          if (scope) {
            setWorkspaceOverrides((current) => ({
              ...current,
              [chatId]: normalizeWorkspaceScope(scope),
            }));
          }
          navigate({
            view: targetView,
            activeKey: `websocket:${chatId}`,
            settingsSection: "overview",
          });
        } catch (error) {
          ensureChatInFlightRef.current = false;
          console.error("Failed to open workspace chat", error);
          if (error instanceof Error && error.message.startsWith("workspace_scope_rejected:")) {
            setWorkspaceError(t("errors.workspaceScopeRejected.body"));
          }
        }
      })();
    },
    [
      activeKey,
      activeWorkspaceScope,
      createChat,
      navigate,
      openProjectWorkspace,
      sessions,
      sidebarState.recent_projects,
      t,
      view,
      workspaces?.default_scope,
    ],
  );

  useEffect(() => {
    if (activeKey) ensureChatInFlightRef.current = false;
  }, [activeKey]);

  useEffect(() => {
    if (loading) return;
    if (view !== "crm") return;
    if (activeKey) return;
    ensureActiveChat(view);
  }, [activeKey, ensureActiveChat, loading, view]);

  const onForkChat = useCallback(async (
    sourceChatId: string,
    beforeUserIndex: number,
  ) => {
    try {
      const sourceSession = sessions.find((session) => session.chatId === sourceChatId);
      const sourceTitle = sourceSession
        ? displayTitle(sourceSession, sidebarState.title_overrides, t("chat.newChat"))
        : t("chat.newChat");
      const chatId = await forkChat(
        sourceChatId,
        beforeUserIndex,
        t("chat.forkTitle", { title: sourceTitle }),
        sourceSession?.workspaceScope,
      );
      const forkedScope = sourceSession?.workspaceScope;
      if (forkedScope) {
        setWorkspaceOverrides((current) => ({
          ...current,
          [chatId]: normalizeWorkspaceScope(forkedScope),
        }));
      }
      navigate({
        view: isWorkbenchView(view) ? view : "chat",
        activeKey: `websocket:${chatId}`,
        settingsSection: "overview",
      });
      setMobileSidebarOpen(false);
      return chatId;
    } catch (e) {
      console.error("Failed to fork chat", e);
      return null;
    }
  }, [forkChat, navigate, sessions, sidebarState.title_overrides, t, view]);

  const onRevertResubmit = useCallback(
    async (beforeUserIndex: number, content: string) => {
      const sourceChatId = activeSession?.chatId;
      if (!sourceChatId || !content.trim()) return;
      const chatId = await onForkChat(sourceChatId, beforeUserIndex);
      if (!chatId) return;
      // The fork stays on a workbench desk (Code, Studio) and otherwise lands
      // on Tchat. ThreadShell reads a different auto-send slot in each case,
      // so queue the edited prompt in the one the destination consumes -
      // on Code the edit used to fork and then silently drop the text.
      const next = (current: { nonce: number } | null) => ({
        text: content.trim(),
        nonce: (current?.nonce ?? 0) + 1,
      });
      if (isWorkbenchView(deskView)) {
        setDevAutoSendRequest(next);
      } else {
        setChatAutoSendRequest(next);
      }
    },
    [activeSession?.chatId, deskView, onForkChat],
  );

  // "New chat" is the empty Tchat desk. Reuse an existing blank for this
  // workspace so we never stack empty rows or leave the composer on
  // "Opening a new chat..." while a second click is required in the sidebar.
  const onNewChat = useCallback(() => {
    writeComposerTurnMode("ask");
    const projectPath =
      activeWorkspaceScope?.project_path ??
      workspaces?.default_scope?.project_path ??
      null;
    const reusable = findReusableEmptyChat(sessions, activeKey, projectPath);
    if (reusable) {
      navigate({
        view: "chat",
        activeKey: reusable.key,
        settingsSection: "overview",
      });
    } else {
      navigate(chatShellRoute());
    }
    setDraftWorkspaceScope(null);
    setWorkspaceError(null);
    setSessionSearchOpen(false);
    setMobileSidebarOpen(false);
  }, [
    activeKey,
    activeWorkspaceScope?.project_path,
    navigate,
    sessions,
    workspaces?.default_scope?.project_path,
  ]);

  const onOpenProject = useCallback(
    (projectPath: string, projectName: string) => {
      setSessionSearchOpen(false);
      setMobileSidebarOpen(false);
      // One click: jump to Code on an existing project chat (or create only if
      // none exists). Avoids the old race that needed a second click.
      void openProjectWorkspace(projectPath, projectName, "dev");
    },
    [openProjectWorkspace],
  );

  const onSelectChat = useCallback(
    (key: string) => {
      const selected = sessions.find((session) => session.key === key);
      const selectedChatId = selected?.chatId;
      if (selectedChatId) {
        setUpdatedChatIds((current) => {
          if (!current.has(selectedChatId)) return current;
          const next = new Set(current);
          next.delete(selectedChatId);
          return next;
        });
      }
      const selectedScope = selected?.workspaceScope;
      if (selectedScope) {
        setDraftWorkspaceScope(normalizeWorkspaceScope(selectedScope));
      } else {
        setDraftWorkspaceScope(null);
      }
      setWorkspaceError(null);
      // Reopen the desk that owns this chat: preview slash first
      // (/forge → Code, /ask → full chat, /meeting → Meeting, …), then tag.
      const nextView = resolveChatOpenView(
        key,
        sidebarState.module_by_key,
        view,
        selected?.preview,
      ) as ShellView;
      if (nextView === "chat") {
        writeComposerTurnMode("ask");
        // Drop a stale Code/studio tag so the next click does not bounce back.
        if (sidebarState.module_by_key[key]) {
          void updateSidebarState((current) => {
            if (!(key in current.module_by_key)) return current;
            const module_by_key = { ...current.module_by_key };
            delete module_by_key[key];
            return { ...current, module_by_key };
          });
        }
      }
      navigate({
        view: nextView,
        activeKey: key,
        settingsSection: "overview",
      });
      setMobileSidebarOpen(false);
    },
    [
      navigate,
      sessions,
      sidebarState.module_by_key,
      updateSidebarState,
      view,
    ],
  );

  const onTogglePin = useCallback(
    (key: string) => {
      void updateSidebarState((current) => {
        const pinned = current.pinned_keys.includes(key)
          ? unpinChatKey(current.pinned_keys, key)
          : pinChatKey(current.pinned_keys, key);
        return {
          ...current,
          pinned_keys: pinned,
        };
      });
    },
    [updateSidebarState],
  );

  const onMoveChat = useCallback(
    (
      dragKey: string,
      targetKey: string,
      targetPinned: boolean,
      visibleUnpinnedKeys: string[],
    ) => {
      void updateSidebarState((current) => {
        const next = placeChatKey(
          current.pinned_keys,
          current.chat_order ?? [],
          dragKey,
          targetKey,
          targetPinned,
          visibleUnpinnedKeys,
        );
        return {
          ...current,
          pinned_keys: next.pinnedKeys,
          chat_order: next.chatOrder,
        };
      });
    },
    [updateSidebarState],
  );

  const onForkChatFromSidebar = useCallback(
    (key: string) => {
      const source = sessions.find((session) => session.key === key);
      if (!source) return;
      // Index past the last user turn copies the whole transcript.
      void onForkChat(source.chatId, 1_000_000);
    },
    [onForkChat, sessions],
  );

  const onOpenChatInNewTab = useCallback(
    (key: string) => {
      const hash = `#/chat/${encodeURIComponent(key)}`;
      const url = `${window.location.origin}${window.location.pathname}${hash}`;
      // Never hand tauri.localhost to the OS browser: that host only exists
      // inside the WebView, and window.open there opened a blank Chrome tab.
      const opened = isInternalDesktopUrl(url)
        ? null
        : window.open(url, "_blank", "noopener,noreferrer");
      if (opened) return;
      navigate({
        view: isWorkbenchView(view) ? view : "dev",
        activeKey: key,
        settingsSection: "overview",
      });
    },
    [navigate, view],
  );

  const onToggleUnread = useCallback((chatId: string) => {
    setUpdatedChatIds((current) => {
      const next = new Set(current);
      if (next.has(chatId)) next.delete(chatId);
      else next.add(chatId);
      return next;
    });
  }, []);

  const onRequestRename = useCallback((key: string, label: string) => {
    setPendingRename({ key, label });
  }, []);

  const onConfirmRename = useCallback(
    (title: string) => {
      if (!pendingRename) return;
      const key = pendingRename.key;
      setPendingRename(null);
      void updateSidebarState((current) => {
        const titleOverrides = { ...current.title_overrides };
        const cleaned = title.trim();
        if (cleaned) {
          titleOverrides[key] = cleaned;
        } else {
          delete titleOverrides[key];
        }
        return {
          ...current,
          title_overrides: titleOverrides,
        };
      });
    },
    [pendingRename, updateSidebarState],
  );

  const onToggleGroup = useCallback(
    (groupId: string) => {
      void updateSidebarState((current) => {
        const collapsedGroups = { ...current.collapsed_groups };
        if (groupId === "workspace:chats" || groupId === "date:all") {
          if (collapsedGroups[groupId] === false) {
            delete collapsedGroups[groupId];
          } else {
            collapsedGroups[groupId] = false;
          }
          return {
            ...current,
            collapsed_groups: collapsedGroups,
          };
        }
        if (collapsedGroups[groupId]) {
          delete collapsedGroups[groupId];
        } else {
          collapsedGroups[groupId] = true;
        }
        return {
          ...current,
          collapsed_groups: collapsedGroups,
        };
      });
    },
    [updateSidebarState],
  );

  const onRequestRenameProject = useCallback((key: string, label: string) => {
    setPendingProjectRename({ key, label });
  }, []);

  const onRequestDeleteProject = useCallback((key: string, label: string) => {
    setPendingProjectDelete({ key, label });
  }, []);

  const onConfirmProjectRename = useCallback(
    (title: string) => {
      if (!pendingProjectRename) return;
      const key = pendingProjectRename.key;
      setPendingProjectRename(null);
      void updateSidebarState((current) => {
        const projectNameOverrides = { ...current.project_name_overrides };
        const cleaned = title.trim();
        if (cleaned) {
          projectNameOverrides[key] = cleaned;
        } else {
          delete projectNameOverrides[key];
        }
        return {
          ...current,
          project_name_overrides: projectNameOverrides,
        };
      });
    },
    [pendingProjectRename, updateSidebarState],
  );

  const onConfirmNewProjectFolder = useCallback(
    async (name: string) => {
      setNewProjectFolderOpen(false);
      try {
        // The gateway creates it under the Projects root (~/NavinProjects by
        // default) and validates the name for every host: Linux, WSL,
        // Windows, macOS. An existing folder simply opens.
        const result = await createProjectFolder(token, name, undefined, "", {
          sessionKey: activeKey ?? undefined,
        });
        await openProjectWorkspace(result.path, result.name, "dev");
      } catch (err) {
        const raw = err instanceof Error ? err.message : String(err);
        const staleRoute = /API route not found/i.test(raw);
        notify({
          level: "error",
          source: "request",
          title: t("chat.newProjectFolderFailed", {
            defaultValue: "Could not create the folder",
          }),
          detail: staleRoute
            ? t("chat.newProjectFolderRestart", {
                defaultValue:
                  "Restart Navin so the folder button can talk to this version, then try again.",
              })
            : raw,
        });
      }
    },
    [activeKey, notify, openProjectWorkspace, t, token],
  );

  const onNewChatInProject = useCallback(
    (projectPath: string, projectName: string) => {
      const base = activeWorkspaceScope ?? workspaces?.default_scope;
      const trimmed = projectPath.trim();
      if (!base || !trimmed) {
        void onCreateChat();
        return;
      }
      void onCreateChat(
        normalizeWorkspaceScope({
          ...base,
          project_path: trimmed,
          project_name: projectName.trim() || projectNameFromPath(trimmed),
        }),
        { keepWorkbench: true },
      );
    },
    [activeWorkspaceScope, onCreateChat, workspaces?.default_scope],
  );

  const onArchiveProject = useCallback(
    (keys: string[]) => {
      if (!keys.length) return;
      void updateSidebarState((current) => {
        const archived = new Set(current.archived_keys);
        const pinned = current.pinned_keys.filter((item) => !keys.includes(item));
        for (const key of keys) archived.add(key);
        return {
          ...current,
          pinned_keys: pinned,
          archived_keys: Array.from(archived),
        };
      });
      if (activeKey && keys.includes(activeKey)) {
        const archived = new Set([...sidebarState.archived_keys, ...keys]);
        const next = sessions.find((session) => !archived.has(session.key));
        navigate({
          view: "chat",
          activeKey: next?.key ?? null,
          settingsSection: "overview",
        });
      }
    },
    [activeKey, navigate, sessions, sidebarState.archived_keys, updateSidebarState],
  );

  const onArchivePriorChats = useCallback(
    (key: string) => {
      const keys = priorChatKeys(sessions, key, {
        defaultWorkspacePath: workspaces?.default_scope?.project_path,
        archivedKeys: sidebarState.archived_keys,
      });
      if (!keys.length) return;
      onArchiveProject(keys);
    },
    [
      onArchiveProject,
      sessions,
      sidebarState.archived_keys,
      workspaces?.default_scope?.project_path,
    ],
  );

  const onToggleArchive = useCallback(
    (key: string) => {
      void updateSidebarState((current) => {
        const archived = new Set(current.archived_keys);
        const pinned = current.pinned_keys.filter((item) => item !== key);
        if (archived.has(key)) {
          archived.delete(key);
        } else {
          archived.add(key);
        }
        return {
          ...current,
          pinned_keys: pinned,
          archived_keys: Array.from(archived),
        };
      });
      if (activeKey === key && !sidebarState.archived_keys.includes(key)) {
        const archived = new Set([...sidebarState.archived_keys, key]);
        const next = sessions.find((session) => !archived.has(session.key));
        navigate({
          view: "chat",
          activeKey: next?.key ?? null,
          settingsSection: "overview",
        });
      }
    },
    [activeKey, navigate, sessions, sidebarState.archived_keys, updateSidebarState],
  );

  const onToggleArchived = useCallback(() => {
    void updateSidebarState((current) => ({
      ...current,
      view: {
        ...current.view,
        show_archived: !current.view.show_archived,
      },
    }));
  }, [updateSidebarState]);

  const onOpenSessionSearch = useCallback(() => {
    setMobileSidebarOpen(false);
    setSessionSearchOpen(true);
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const commandShiftO =
        (event.metaKey || event.ctrlKey) && event.shiftKey && !event.altKey;
      if (commandShiftO && event.key.toLowerCase() === "o") {
        event.preventDefault();
        onNewChat();
        return;
      }
      const plainCommandK =
        (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey;
      if (!plainCommandK) return;
      if (event.key.toLowerCase() !== "k") return;
      event.preventDefault();
      onOpenSessionSearch();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onNewChat, onOpenSessionSearch]);

  const onSelectSearchResult = useCallback(
    (key: string) => {
      setSessionSearchOpen(false);
      onSelectChat(key);
    },
    [onSelectChat],
  );

  const onOpenSettings = useCallback((section: SettingsSectionKey = "overview") => {
    setSessionSearchOpen(false);
    const nextSection = isSettingsSectionKey(section) ? section : "overview";
    navigate({ view: "settings", activeKey, settingsSection: nextSection });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenAccountSettings = useCallback(() => {
    onOpenSettings("account");
  }, [onOpenSettings]);

  const onOpenProviderSettings = useCallback(() => {
    onOpenSettings("providers");
  }, [onOpenSettings]);

  const onOpenVoiceSettings = useCallback(() => {
    onOpenSettings("voice");
  }, [onOpenSettings]);

  // Focus mode belongs to the surface that turned it on: leaving the view must
  // never strand the chat column off screen.
  useEffect(() => {
    setWorkbenchFocus(false);
  }, [deskView]);

  useEffect(() => {
    if (DESK_CHAT_OFF_BY_DEFAULT.has(deskView)) setDeskChatOpen(false);
  }, [deskView]);

  const onToggleDeskChat = useCallback(() => {
    if (deskChatOpen) {
      setDeskChatOpen(false);
      return;
    }
    setDeskChatOpen(true);
    const command = DESK_CHAT_COMMAND[deskView];
    if (command) {
      onSeedDevComposer(`${command} `, undefined, { ensureCommand: command });
    }
  }, [deskChatOpen, deskView, onSeedDevComposer]);

  const onOpenDeskChat = useCallback(() => {
    setDeskChatOpen(true);
  }, []);

  // Déconnecté / sans clé : le chemin principal mène au compte (offres +
  // connexion). Un provider BYOK reste accessible via le bandeau secondaire.
  const onOpenModelSettings = useCallback(() => {
    if (!account?.connected) {
      onOpenAccountSettings();
      return;
    }
    onOpenSettings("models");
  }, [account?.connected, onOpenAccountSettings, onOpenSettings]);

  const onOpenTools = useCallback(() => {
    onOpenSettings("tools");
  }, [onOpenSettings]);

  const onOpenAutomations = useCallback(() => {
    onOpenSettings("automations");
  }, [onOpenSettings]);

  const onOpenSkills = useCallback(() => {
    onOpenSettings("skills");
  }, [onOpenSettings]);

  const onOpenTemplates = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({
      view: "dev",
      activeKey,
      settingsSection: "overview",
      openTemplatesPanel: true,
    });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenRisklensStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "risklens", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenDev = useCallback(() => {
    // Code is always agent mode; ask is reserved for Studio → Tchat.
    writeComposerTurnMode("agent");
    setSessionSearchOpen(false);
    setMobileSidebarOpen(false);

    // Settings / Tools / Apps sit on top of Code. Clicking Code must close
    // that overlay instead of no-op'ing because the desk underneath is
    // already "dev".
    if (isEphemeralShellView(view)) {
      navigate({
        view: "dev",
        activeKey,
        settingsSection: "overview",
      });
      return;
    }

    const target = resolveLastCodeOpen({
      view,
      currentProjectPath: activeWorkspaceScope?.project_path ?? null,
      last: readLastDevContext(),
      recentProjects: sidebarState.recent_projects,
      defaultPath: workspaces?.default_scope?.project_path,
      currentChatKey: activeKey,
      knownChatKeys: sessions.map((session) => session.key),
      samePath: sameWorkspacePath,
      isInternalPath: isNavinInternalPath,
    });

    if (target.action === "noop") return;
    if (target.action === "project") {
      void openProjectWorkspace(target.path, target.name, "dev");
      return;
    }
    if (target.action === "chat") {
      navigate({
        view: "dev",
        activeKey: target.chatKey,
        settingsSection: "overview",
      });
      return;
    }
    navigate({ view: "dev", activeKey: activeKey, settingsSection: "overview" });
  }, [
    activeKey,
    activeWorkspaceScope?.project_path,
    navigate,
    openProjectWorkspace,
    sessions,
    sidebarState.recent_projects,
    view,
    workspaces?.default_scope?.project_path,
  ]);

  const onResumeFromProjectHome = useCallback(
    (seed: string) => {
      setSessionSearchOpen(false);
      onSeedDevComposer(seed);
      navigate({ view: "dev", activeKey, settingsSection: "overview" });
      setMobileSidebarOpen(false);
    },
    [activeKey, navigate, onSeedDevComposer],
  );

  const onOpenScrapingStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "scraping", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenContentStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "content", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenMarketingStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "marketing", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenMontageStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "montage", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenAdsStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "ads", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenSeoStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "seo", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenLeadsStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "leads", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenTendersStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "tenders", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenCareerStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "career", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenTradingStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "trading", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenCrmStudio = useCallback(() => {
    setSessionSearchOpen(false);
    setMobileSidebarOpen(false);
    ensureActiveChat("crm");
  }, [ensureActiveChat]);

  const onOpenMeetingStudio = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "meeting", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  const onOpenNotes = useCallback(() => {
    setSessionSearchOpen(false);
    navigate({ view: "notes", activeKey, settingsSection: "overview" });
    setMobileSidebarOpen(false);
  }, [activeKey, navigate]);

  // Cross-module deep link (Meetings -> the note a meeting was filed into).
  const [notesOpenRequest, setNotesOpenRequest] = useState<
    { id: string; nonce: number } | null
  >(null);
  const onOpenNoteById = useCallback(
    (noteId: string) => {
      setNotesOpenRequest({ id: noteId, nonce: Date.now() });
      onOpenNotes();
    },
    [onOpenNotes],
  );

  const onTranscribeMeetingAudio = useCallback(
    (dataUrl: string, options?: { durationMs?: number }) =>
      client.transcribeAudio(dataUrl, options),
    [client],
  );

  // Reading a summary aloud goes through the realtime voice session, so it is
  // only offered when the plan allows it and a TTS provider is configured.
  const meetingSpeechAvailable = Boolean(
    settingsSnapshot?.voice?.realtime_allowed
      && settingsSnapshot?.voice?.tts_provider_configured,
  );
  const onSpeakMeetingText = useCallback(
    (text: string) => speakOnce(client, text),
    [client],
  );

  const onSettingsSectionChange = useCallback(
    (section: SettingsSectionKey) => {
      navigate({
        view: shellViewForSettingsSection(section),
        activeKey,
        settingsSection: section,
      });
    },
    [activeKey, navigate],
  );

  const onBackToChat = useCallback(() => {
    setMobileSidebarOpen(false);
    // Prefer the desk captured when opening Settings / Account / Apps
    // (Project stays Project - do not bounce to Code via module_by_key).
    const overlayReturn = overlayReturnRouteRef.current;
    if (overlayReturn && !isEphemeralShellView(overlayReturn.view)) {
      overlayReturnRouteRef.current = null;
      navigate(overlayReturn);
      return;
    }
    // Settings / Apps / … are ephemeral overlays: return to the work module
    // (Code, Montage, …) + chat the user left, not plain chat home.
    const lastHash = readLastShellRouteHash();
    if (lastHash) {
      const normalized = normalizeShellHash(lastHash);
      if (window.location.hash !== normalized && !shellHashesEqual(window.location.hash, normalized)) {
        window.location.hash = normalized;
        return;
      }
    }
    const nextKey =
      activeKey && sessions.some((session) => session.key === activeKey)
        ? activeKey
        : (sessions[0]?.key ?? null);
    if (!nextKey) {
      navigate({
        view: "chat",
        activeKey: null,
        settingsSection: "overview",
      });
      return;
    }
    const nextView = resolveChatOpenView(
      nextKey,
      sidebarState.module_by_key,
      "chat",
    ) as ShellView;
    navigate({
      view: nextView,
      activeKey: nextKey,
      settingsSection: "overview",
    });
  }, [activeKey, navigate, sessions, sidebarState.module_by_key]);

  const onRestart = useCallback(() => {
    const chatId = activeSession?.chatId ?? client.defaultChatId;
    if (!chatId) return;
    restartSawDisconnectRef.current = false;
    setIsRestarting(true);
    rememberRestartRoute();
    try {
      window.localStorage.setItem(RESTART_STARTED_KEY, String(Date.now()));
    } catch {
      // ignore storage errors
    }
    client.sendMessage(chatId, "/restart");
  }, [activeSession?.chatId, client]);

  const visibleChatId = activeSession?.chatId ?? null;
  useEffect(() => {
    return client.onRuntimeModelUpdate((modelName, _preset, meta) => {
      // This label describes the chat on screen. Background sessions kept
      // overwriting it, so every thread ended up advertising the model of
      // whichever session had spoken last.
      if (meta?.chatId && visibleChatId && meta.chatId !== visibleChatId) return;
      if (meta?.reason?.startsWith("task:")) return;
      onModelNameChange(modelName);
    });
  }, [client, onModelNameChange, visibleChatId]);

  useEffect(() => {
    return client.onRunStatus((chatId, startedAt) => {
      if (startedAt != null) {
        const nextRunning = new Set(runningChatIdsRef.current);
        nextRunning.add(chatId);
        runningChatIdsRef.current = nextRunning;
        setRunningChatIds(nextRunning);
        setUpdatedChatIds((current) => {
          if (!current.has(chatId)) return current;
          const next = new Set(current);
          next.delete(chatId);
          return next;
        });
        return;
      }

      if (!runningChatIdsRef.current.has(chatId)) return;
      const nextRunning = new Set(runningChatIdsRef.current);
      nextRunning.delete(chatId);
      runningChatIdsRef.current = nextRunning;
      setRunningChatIds(nextRunning);
      setUpdatedChatIds((current) => {
        const next = new Set(current);
        if (activeChatIdRef.current === chatId) {
          next.delete(chatId);
        } else {
          next.add(chatId);
        }
        return next;
      });
    });
  }, [client]);

  useEffect(() => {
    return client.onStatus((status) => {
      const startedAt = (() => {
        try {
          return Number(window.localStorage.getItem(RESTART_STARTED_KEY) ?? "0");
        } catch {
          return 0;
        }
      })();
      if (!startedAt) return;
      if (status !== "open") {
        restartSawDisconnectRef.current = true;
        return;
      }
      const elapsedMs = Date.now() - startedAt;
      if (!restartSawDisconnectRef.current && elapsedMs < 1500) return;
      try {
        window.localStorage.removeItem(RESTART_STARTED_KEY);
        window.localStorage.removeItem(RESTART_ROUTE_KEY);
      } catch {
        // ignore storage errors
      }
      setIsRestarting(false);
      setRestartToast(t("app.restart.completed", { seconds: (elapsedMs / 1000).toFixed(1) }));
      window.setTimeout(() => setRestartToast(null), 3_500);
    });
  }, [client, t]);

  const onTurnEnd = useDeferredTitleRefresh(activeSession, refresh);

  const onConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    const key = pendingDelete.key;
    const hasAutomations = (pendingDelete.automations?.length ?? 0) > 0;
    const deletingActive = activeKey === key;
    const currentIndex = sessions.findIndex((s) => s.key === key);
    const fallbackKey = deletingActive
      ? (sessions[currentIndex + 1]?.key ?? sessions[currentIndex - 1]?.key ?? null)
      : activeKey;
    try {
      const result = await deleteChat(
        key,
        hasAutomations ? { deleteAutomations: true } : undefined,
      );
      if (result.blocked_by_automations) {
        setPendingDelete({
          ...pendingDelete,
          automations: result.automations ?? [],
        });
        return;
      }
      setPendingDelete(null);
      if (deletingActive) {
        navigate({
          view: "chat",
          activeKey: fallbackKey,
          settingsSection: "overview",
        }, { replace: true });
      }
    } catch (e) {
      console.error("Failed to delete session", e);
    }
  }, [pendingDelete, deleteChat, activeKey, navigate, sessions]);

  const onRequestDelete = useCallback(async (key: string, label: string) => {
    let automations: SessionAutomationJob[] = [];
    try {
      automations = await getSessionAutomations(key);
    } catch {
      // Delete remains protected by the backend block; prefetch only improves the first prompt.
    }
    setPendingDelete({ key, label, automations });
  }, [getSessionAutomations]);

  const onConfirmProjectDelete = useCallback(async () => {
    if (!pendingProjectDelete) return;
    const projectKey = pendingProjectDelete.key;
    setPendingProjectDelete(null);
    const targets = sessions.filter((s) => {
      const path = s.workspaceScope?.project_path;
      return path && normalizeWorkspacePath(path) === projectKey;
    });
    const deletingActive = targets.some((s) => s.key === activeKey);
    for (const s of targets) {
      try {
        await deleteChat(s.key, { deleteAutomations: true });
      } catch (e) {
        console.error("Failed to delete project session", s.key, e);
      }
    }
    if (deletingActive) {
      const remaining = sessions.find(
        (s) => !targets.some((target) => target.key === s.key),
      );
      navigate({
        view: "chat",
        activeKey: remaining?.key ?? null,
        settingsSection: "overview",
      }, { replace: true });
    }
  }, [pendingProjectDelete, sessions, activeKey, deleteChat, navigate]);

  const visiblePairingRequests = useMemo(
    () => {
      const now = Date.now();
      return pairingRequests.filter((request) => {
        const snoozedUntil = snoozedPairingCodes.get(request.code);
        return !snoozedUntil || snoozedUntil <= now;
      });
    },
    [pairingRequests, snoozedPairingCodes],
  );

  const onPairingAction = useCallback(
    async (action: "approve" | "deny", code: string) => {
      setPairingBusyCode(code);
      setPairingError(null);
      try {
        const payload = await runPairingAction(token, action, code);
        setPairingRequests(Array.isArray(payload.requests) ? payload.requests : []);
        setSnoozedPairingCodes((current) => {
          if (!current.has(code)) return current;
          const next = new Map(current);
          next.delete(code);
          return next;
        });
      } catch (e) {
        setPairingError((e as Error).message);
        void refreshPairingRequests();
      } finally {
        setPairingBusyCode(null);
      }
    },
    [refreshPairingRequests, token],
  );

  const onDismissPairingRequest = useCallback((code: string) => {
    setSnoozedPairingCodes((current) => {
      const snoozedUntil = Date.now() + PAIRING_DISMISS_SNOOZE_MS;
      if (current.get(code) === snoozedUntil) return current;
      const next = new Map(current);
      next.set(code, snoozedUntil);
      return next;
    });
  }, []);

  const headerTitle = activeSession
    ? sidebarState.title_overrides[activeSession.key] ||
      activeSession.title ||
      deriveTitle(activeSession.preview, t("chat.newChat"))
    : t("app.brand");
  const productModule = useMemo(() => {
    if (view === "chat") return null;
    if (view === "dev") return "code";
    if (
      view === "risklens"
      || view === "scraping"
      || view === "content"
      ||       view === "marketing"
      || view === "montage"
      || view === "ads"
      || view === "seo"
      || view === "leads"
      || view === "tenders"
      || view === "career"
      || view === "trading"
      || view === "meeting"
      || view === "ops"
      || view === "notes"
      || view === "crm"
    ) {
      return view;
    }
    return null;
  }, [view]);
  useEffect(() => {
    if (view === "settings") {
      document.title = t("app.documentTitle.chat", {
        title: t("settings.sidebar.title"),
      });
      return;
    }
    if (view === "tools") {
      document.title = t("app.documentTitle.chat", {
        title: t("sidebar.tools", { defaultValue: "Tools & Mcp" }),
      });
      return;
    }
    if (view === "apps") {
      document.title = t("app.documentTitle.chat", {
        title: t("settings.nav.apps", { defaultValue: "Apps" }),
      });
      return;
    }
    if (view === "automations") {
      document.title = t("app.documentTitle.chat", {
        title: t("settings.nav.automations", { defaultValue: "Automations" }),
      });
      return;
    }
    if (view === "skills") {
      document.title = t("app.documentTitle.chat", {
        title: t("settings.nav.skills", { defaultValue: "Skills" }),
      });
      return;
    }
    if (view === "templates") {
      document.title = t("app.documentTitle.chat", {
        title: t("settings.nav.templates", { defaultValue: "Templates" }),
      });
      return;
    }
    if (view === "project") {
      document.title = t("app.documentTitle.chat", {
        title: t("sidebar.projectHome"),
      });
      return;
    }
    if (isWorkbenchView(view)) {
      const workbenchTitles: Partial<Record<ShellView, string>> = {
        risklens: t("sidebar.risklensStudio", { defaultValue: "RiskLens" }),
        dev: t("sidebar.dev", { defaultValue: "Dev" }),
        scraping: t("sidebar.scrapingStudio", { defaultValue: "Scraping" }),
        content: t("sidebar.contentStudio", { defaultValue: "Documents" }),
        marketing: t("sidebar.marketingStudio", { defaultValue: "Marketing" }),
        montage: t("sidebar.montageStudio", { defaultValue: "Montage" }),
        ads: t("sidebar.adsStudio", { defaultValue: "Ads" }),
        seo: t("sidebar.seoStudio", { defaultValue: "SEO" }),
        leads: t("sidebar.leadsStudio", { defaultValue: "Leads" }),
        tenders: t("sidebar.tendersStudio", { defaultValue: "Tenders" }),
        career: t("sidebar.careerStudio", { defaultValue: "Career" }),
        trading: t("sidebar.tradingStudio", { defaultValue: "Trading" }),
        meeting: t("sidebar.meetingStudio", { defaultValue: "Meeting" }),
        ops: t("sidebar.opsStudio", { defaultValue: "Ops" }),
        notes: t("sidebar.notes", { defaultValue: "Notes" }),
        crm: t("sidebar.crm", { defaultValue: "CRM" }),
      };
      document.title = t("app.documentTitle.chat", {
        title: workbenchTitles[view] ?? "Navin",
      });
      return;
    }
    document.title = activeSession
      ? t("app.documentTitle.chat", { title: headerTitle })
      : t("app.documentTitle.base");
  }, [activeSession, headerTitle, i18n.resolvedLanguage, t, view]);

  const sidebarUtilityView = settingsOverlay ? deskView : view;
  const sidebarProps = {
    sessions,
    activeKey,
    loading,
    onNewChat,
    onSelect: onSelectChat,
    onRequestDelete,
    onTogglePin,
    onMoveChat,
    onForkChat: onForkChatFromSidebar,
    onOpenInNewTab: onOpenChatInNewTab,
    onToggleUnread,
    onArchivePriorChats,
    onRequestRename,
    onToggleArchive,
    onToggleGroup,
    onRequestRenameProject,
    onRequestDeleteProject,
    onArchiveProject,
    onNewChatInProject,
    onOpenProject,
    onCreateProjectFolder: () => setNewProjectFolderOpen(true),
    onOpenSettings,
    onOpenAccount: () => onOpenSettings("account"),
    onOpenSearch: onOpenSessionSearch,
    onOpenRisklensStudio,
    onOpenDev,
    onOpenScrapingStudio,
    onOpenContentStudio,
    onOpenMarketingStudio,
    onOpenMontageStudio,
    onOpenAdsStudio,
    onOpenSeoStudio,
    onOpenTendersStudio,
    onOpenCareerStudio,
    onOpenTradingStudio,
    onOpenLeadsStudio,
    onOpenCrmStudio,
    onOpenMeetingStudio,
    onOpenNotes,
    theme,
    onToggleTheme: toggle,
    activeUtility:
      sidebarUtilityView === "tools" ||
      sidebarUtilityView === "apps" ||
      sidebarUtilityView === "automations" ||
      sidebarUtilityView === "skills" ||
      sidebarUtilityView === "templates" ||
      sidebarUtilityView === "project" ||
      sidebarUtilityView === "risklens" ||
      sidebarUtilityView === "dev" ||
      sidebarUtilityView === "chat" ||
      sidebarUtilityView === "scraping" ||
      sidebarUtilityView === "content" ||
      sidebarUtilityView === "marketing" ||
      sidebarUtilityView === "montage" ||
      sidebarUtilityView === "ads" ||
      sidebarUtilityView === "seo" ||
      sidebarUtilityView === "leads" ||
      sidebarUtilityView === "tenders" ||
      sidebarUtilityView === "career" ||
      sidebarUtilityView === "trading" ||
      sidebarUtilityView === "meeting" ||
      sidebarUtilityView === "ops" ||
      sidebarUtilityView === "notes" ||
      sidebarUtilityView === "crm"
        ? sidebarUtilityView
        : null,
    settingsActive: view === "settings",
    onToggleArchived,
    pinnedKeys: sidebarState.pinned_keys,
    chatOrder: sidebarState.chat_order,
    archivedKeys: sidebarState.archived_keys,
    titleOverrides: sidebarState.title_overrides,
    projectNameOverrides: sidebarState.project_name_overrides,
    collapsedGroups: sidebarState.collapsed_groups,
    runningChatIds: runningChatIdList,
    updatedChatIds: updatedChatIdList,
    viewState: sidebarState.view,
    showArchived: sidebarState.view.show_archived,
    archivedCount: sidebarState.archived_keys.length,
    defaultWorkspacePath: workspaces?.default_scope.project_path ?? null,
    recentProjects: sidebarState.recent_projects ?? [],
  };
  // Collapsed host sidebar keeps the icon rail (same as browser). Tauri used to
  // go fully off-canvas (width 0), which let Code's explorer swallow the shell
  // and hid the Studio / chats / tools icons the user expects to keep.
  const hostSidebarFlowWidth = hostSidebarOpen
    ? SIDEBAR_WIDTH
    : SIDEBAR_RAIL_WIDTH;

  useEffect(() => {
    document.documentElement.classList.toggle("native-host", showHostChrome);
    return () => {
      document.documentElement.classList.remove("native-host");
    };
  }, [showHostChrome]);
  useChatDensityAttribute();

  useEffect(() => {
    const idle = window.setTimeout(() => {
      void import("@/components/settings/SettingsView");
    }, 800);
    return () => window.clearTimeout(idle);
  }, []);

  return (
    <ThemeProvider theme={theme}>
      <div
        className={cn(
          "relative h-full w-full overflow-hidden",
          showHostChrome && "host-window-shell",
        )}
        style={
          {
            "--navin-host-sidebar": `${showMainSidebar ? hostSidebarFlowWidth : 0}px`,
          } as CSSProperties
        }
      >
        {showHostChrome ? (
          <HostChrome
            rightAction={
              // Workbench toolbars already own the top-right (hide chat / maximize).
              // A HostChrome theme button sat on top of them. Theme stays in the sidebar.
              view === "chat" || isWorkbenchView(view) ? undefined : (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={t("thread.header.toggleTheme")}
                  onClick={toggle}
                  className="h-8 w-8 rounded-full text-muted-foreground/85 hover:bg-accent/40 hover:text-foreground"
                >
                  {theme === "dark" ? (
                    <Sun className="h-4 w-4" />
                  ) : (
                    <Moon className="h-4 w-4" />
                  )}
                </Button>
              )
            }
          />
        ) : null}
        <div
          className={cn(
            "relative flex h-full w-full overflow-hidden",
          )}
        >
          {/* Host sidebar: in normal flow, so the thread area width stays honest. */}
          {showMainSidebar ? (
            <aside
              data-testid="host-sidebar-flow"
              className={cn(
                "relative z-20 hidden shrink-0 overflow-hidden lg:block",
                "transition-[width] duration-300 ease-out",
              )}
              style={{
                width: hostSidebarFlowWidth,
              }}
            >
              <div
                className={cn(
                  "absolute inset-y-0 left-0 h-full w-full overflow-hidden",
                  showHostChrome
                    ? "host-sidebar-glass"
                    : "bg-sidebar shadow-inner-right",
                )}
              >
                <Sidebar
                  {...sidebarProps}
                  collapsed={!hostSidebarOpen}
                  hostChromeInset={showHostChrome}
                  onCollapse={closeHostSidebar}
                  onExpand={openHostSidebar}
                />
              </div>
            </aside>
          ) : null}

          {showMainSidebar ? (
            <Sheet
              open={mobileSidebarOpen}
              onOpenChange={(open) => setMobileSidebarOpen(open)}
            >
              <SheetContent
                side="left"
                showCloseButton={false}
                aria-describedby={undefined}
                className="p-0 lg:hidden"
                style={{ width: MOBILE_SIDEBAR_WIDTH, maxWidth: MOBILE_SIDEBAR_WIDTH }}
              >
                <SheetTitle className="sr-only">{t("sidebar.navigation")}</SheetTitle>
                <Sidebar
                  {...sidebarProps}
                  onCollapse={closeMobileSidebar}
                  containActionMenus
                />
              </SheetContent>
            </Sheet>
          ) : null}

          <SessionSearchDialog
            open={sessionSearchOpen}
            onOpenChange={setSessionSearchOpen}
            sessions={sessions}
            activeKey={activeKey}
            loading={loading}
            titleOverrides={sidebarState.title_overrides}
            onSelect={onSelectSearchResult}
          />
        <main
          ref={shellMainRef}
          className="relative flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-background"
        >
            {/* Workbench views have their own toolbar at the top and already
                surface "Model not configured" in the composer badge. */}
            {providerSetupNeeded && !showFirstRunWizard && view === "chat" ? (
              <div
                className="pointer-events-none absolute inset-x-0 top-0 z-40 flex justify-center px-4 pt-3"
              >
                <div
                  role="alert"
                  className="pointer-events-auto flex w-full max-w-2xl flex-col gap-3 rounded-xl border border-border bg-background/95 px-4 py-3 shadow-lg backdrop-blur"
                >
                  <div className="flex items-start gap-3">
                    <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-foreground" aria-hidden />
                    <p className="min-w-0 flex-1 text-[13px] leading-snug text-pretty text-foreground">
                      {t("providerSetup.message", {
                        defaultValue:
                          "No model is ready yet. Start free with your own OpenRouter account, subscribe for managed models, or configure your own providers.",
                      })}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 pl-7">
                    <button
                      type="button"
                      onClick={() => setFreeSetupOpen(true)}
                      className="rounded-lg border border-border bg-foreground px-3 py-1.5 text-[12px] font-medium text-background transition-opacity hover:opacity-85 active:scale-[0.96]"
                    >
                      {t("providerSetup.freeAction", {
                        defaultValue: "Start free (OpenRouter)",
                      })}
                    </button>
                    <button
                      type="button"
                      onClick={onOpenAccountSettings}
                      className="rounded-lg border border-border bg-background px-3 py-1.5 text-[12px] font-medium text-foreground transition-colors hover:bg-muted active:scale-[0.96]"
                    >
                      {t("providerSetup.subscribeAction", {
                        defaultValue: "Subscribe or sign in",
                      })}
                    </button>
                    <button
                      type="button"
                      onClick={onOpenProviderSettings}
                      className="rounded-lg border border-border bg-background px-3 py-1.5 text-[12px] font-medium text-foreground transition-colors hover:bg-muted active:scale-[0.96]"
                    >
                      {t("providerSetup.action", {
                        defaultValue: "Configure a provider",
                      })}
                    </button>
                  </div>
                </div>
              </div>
            ) : null}
            {isWorkbenchView(deskView) && (
              <div
                className="absolute inset-y-0 flex"
                style={
                  devDocked
                    ? // Code: the workbench keeps the whole shell (explorer +
                      // status bar stay exactly in place, panel or rail on the
                      // right); the chat overlays the center it leaves empty.
                      { left: 0, right: 0 }
                    : {
                        // Narrow or focus mode: workbench is full-bleed; the
                        // chat overlays as a sheet or steps aside entirely.
                        // Wide: reserve the chat column on the right.
                        left: 0,
                        right: isNarrowWorkbench || hideSideChat ? 0 : devChatWidth,
                      }
                }
              >
                <div
                  className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
                  data-desk-scroll-host=""
                >
                <Suspense
                  fallback={
                    <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                      Loading workspace
                    </div>
                  }
                >
                {deskView === "dev" ? (
                  <DevWorkbench
                    sessionKey={activeKey}
                    projectPath={activeWorkspaceScope?.project_path ?? null}
                    projectName={activeWorkspaceScope?.project_name ?? null}
                    recentProjects={sidebarState.recent_projects ?? []}
                    sessions={sessions}
                    onSelectProject={onSelectDevProject}
                    openFileRequest={devOpenFileRequest}
                    previewOpenRequest={devPreviewOpenRequest}
                    projectHomeRequest={devProjectHomeRequest}
                    templatesRequest={devTemplatesRequest}
                    evolveRequest={devEvolveRequest}
                    onOpenFileRequestHandled={clearDevOpenFileRequest}
                    onPreviewOpenRequestHandled={clearDevPreviewOpenRequest}
                    onProjectHomeRequestHandled={clearDevProjectHomeRequest}
                    onTemplatesRequestHandled={clearDevTemplatesRequest}
                    onEvolveRequestHandled={clearDevEvolveRequest}
                    chatOpen
                    onRunAction={onRunDevAction}
                    onSeedChat={onSeedDevComposer}
                    onOpenChat={onSelectChat}
                    onResumeProjectGoal={onResumeFromProjectHome}
                    onMaximizeChat={
                      !isNarrowWorkbench ? onToggleChatMaximized : undefined
                    }
                    onRevealWorkbench={
                      !isNarrowWorkbench ? onRevealDevWorkbench : undefined
                    }
                    collapsed={devChatMaximized}
                    railDensity={railDensity}
                    onToggleRailDensity={onToggleRailDensity}
                    dockRight={devDocked}
                    panelWidth={devPanelEffective}
                    projectsReady={!loading}
                    onExplorerWidthChange={setDevExplorerWidth}
                  />
                ) : deskView === "montage" ? (
                  <MontageWorkbench
                    sessionKey={activeKey}
                    projectPath={activeWorkspaceScope?.project_path ?? null}
                    projectName={activeWorkspaceScope?.project_name ?? null}
                    recentProjects={sidebarState.recent_projects ?? []}
                    onSelectProject={onSelectDevProject}
                    chatOpen
                    onRun={onRunDevAction}
                    onSeed={onSeedDevComposer}
                    onOpenSettings={onOpenSettings}
                  />
                ) : deskView === "notes" ? (
                  <NotesWorkbench
                    chatOpen={!hideSideChat}
                    onToggleChat={onToggleDeskChat}
                    onSeed={(text) => {
                      onOpenDeskChat();
                      onSeedDevComposer(text, undefined, { replace: true });
                    }}
                    onRun={(text) => {
                      onOpenDeskChat();
                      onRunDevAction(text);
                    }}
                    openRequest={notesOpenRequest}
                  />
                ) : deskView === "crm" ? (
                  <CrmWorkbench
                    sessionKey={activeKey}
                    projectPath={activeWorkspaceScope?.project_path ?? null}
                    projectName={activeWorkspaceScope?.project_name ?? null}
                    recentProjects={sidebarState.recent_projects ?? []}
                    onSelectProject={onSelectDevProject}
                  />
                ) : deskView === "tenders" ? (
                  <TendersWorkspace
                    chatOpen={!hideSideChat}
                    onToggleChat={onToggleDeskChat}
                    noticeId={tenderNotice}
                    pane={tenderPane}
                    onPane={(pane) =>
                      navigate(
                        {
                          view: "tenders",
                          activeKey,
                          settingsSection: "overview",
                          tenderPane: pane,
                        },
                        { replace: true },
                      )
                    }
                    onNotice={(id) =>
                      navigate(
                        {
                          view: "tenders",
                          activeKey,
                          settingsSection: "overview",
                          tenderNotice: id || undefined,
                          tenderPane: id ? "tenders" : tenderPane,
                        },
                        { replace: true },
                      )
                    }
                    onSeed={(text) => {
                      onOpenDeskChat();
                      onSeedDevComposer(text, undefined, { replace: true });
                    }}
                  />
                ) : deskView === "career" ? (
                  <CareerWorkspace
                    chatOpen={!hideSideChat}
                    onToggleChat={onToggleDeskChat}
                    deskPane={careerPane}
                    jobId={careerJob}
                    onDeskPane={(pane, job) =>
                      navigate(
                        {
                          view: "career",
                          activeKey,
                          settingsSection: "overview",
                          careerPane: pane,
                          careerJob: job || undefined,
                        },
                        { replace: true },
                      )
                    }
                    onSeed={(text) => {
                      onOpenDeskChat();
                      onSeedDevComposer(text, undefined, { replace: true });
                    }}
                  />
                ) : deskView === "leads" ? (
                  <LeadsWorkspace
                    chatOpen={!hideSideChat}
                    onToggleChat={onToggleDeskChat}
                    deskPane={leadsPane}
                    leadId={leadsLead}
                    projectPath={
                      selectedProjectScope(activeWorkspaceScope, workspaces?.default_scope ?? null)
                        ?.project_path
                      ?? Object.keys(sidebarState.module_by_path ?? {}).find((path) => {
                        const name = path.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "";
                        return name.toLowerCase() !== "navinprojects";
                      })
                      ?? sidebarState.recent_projects?.find((entry) => {
                        const name =
                          (entry.path || "").replace(/\\/g, "/").split("/").filter(Boolean).pop() || "";
                        return (
                          name.toLowerCase() !== "navinprojects"
                          && !sameWorkspacePath(entry.path, workspaces?.default_scope?.project_path)
                        );
                      })?.path
                      ?? sessions.find((session) => {
                        const path = session.workspaceScope?.project_path || "";
                        const name = path.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "";
                        return (
                          name.toLowerCase() !== "navinprojects"
                          && !sameWorkspacePath(path, workspaces?.default_scope?.project_path)
                        );
                      })?.workspaceScope?.project_path
                      ?? null
                    }
                    recentProjects={[
                      ...(sidebarState.recent_projects ?? []),
                      ...Object.keys(sidebarState.module_by_path ?? {}).map((path) => ({ path })),
                      ...sessions
                        .map((session) => ({ path: session.workspaceScope?.project_path || "" }))
                        .filter((row) => row.path),
                    ]}
                    onDeskPane={(pane, lead) =>
                      navigate(
                        {
                          view: "leads",
                          activeKey,
                          settingsSection: "overview",
                          leadsPane: pane,
                          leadsLead: lead || undefined,
                        },
                        { replace: true },
                      )
                    }
                    onSeed={(text) => {
                      onOpenDeskChat();
                      onSeedDevComposer(text, undefined, { replace: true });
                    }}
                  />
                ) : deskView === "trading" ? (
                  <TradingWorkspace
                    chatOpen={!hideSideChat}
                    onToggleChat={onToggleDeskChat}
                    onSeed={(text) => {
                      onOpenDeskChat();
                      onSeedDevComposer(text, undefined, { replace: true });
                    }}
                  />
                ) : deskView === "marketing" ? (
                  <MarketingWorkspace
                    chatOpen={!hideSideChat}
                    onToggleChat={onToggleDeskChat}
                    projectPath={activeWorkspaceScope?.project_path ?? null}
                    projectName={activeWorkspaceScope?.project_name ?? null}
                    recentProjects={sidebarState.recent_projects ?? []}
                    onSeed={(text) => {
                      onOpenDeskChat();
                      onSeedDevComposer(text, undefined, { replace: true });
                    }}
                  />
                ) : deskView === "meeting" ? (
                  <MeetingWorkbench
                    chatOpen={!hideSideChat}
                    onToggleChat={onToggleDeskChat}
                    onSeed={(text) => {
                      onOpenDeskChat();
                      onSeedDevComposer(text, undefined, { replace: true });
                    }}
                    onTranscribeAudio={onTranscribeMeetingAudio}
                    transcription={settingsSnapshot?.transcription}
                    onOpenVoiceSettings={onOpenVoiceSettings}
                    onSpeak={meetingSpeechAvailable ? onSpeakMeetingText : undefined}
                    sessionKey={activeKey}
                    onOpenNote={onOpenNoteById}
                  />
                ) : (
                  <StudioWorkspace
                    module={deskView as StudioModule}
                    chatOpen={deskView === "scraping" ? !hideSideChat : true}
                    onToggleChat={deskView === "scraping" ? onToggleDeskChat : undefined}
                    onRun={onRunDevAction}
                    onSeed={(text, files, options) =>
                      onSeedDevComposer(text, files, {
                        replace: options?.replace ?? true,
                        ...options,
                      })
                    }
                    projectPath={activeWorkspaceScope?.project_path ?? null}
                    projectName={activeWorkspaceScope?.project_name ?? null}
                    recentProjects={sidebarState.recent_projects ?? []}
                    onSelectProject={onSelectDevProject}
                  />
                )}
                </Suspense>
                </div>
                {/* Where every rendered result lands, for every module: beside
                    the workbench, never on top of it. An overlay covered the
                    tabs, the file tree and the toolbar, so opening a file was
                    impossible while a result was on screen. `empty:hidden`
                    means it costs no width until something is rendered into
                    it. */}
                <div
                  ref={setArtifactCanvasHost}
                  className={cn(
                    "flex w-[46%] min-w-[320px] max-w-[900px] shrink-0 flex-col overflow-hidden border-l border-border/70 empty:hidden",
                    // Docked Code: the right column already owns that space.
                    // Artifacts render inline in the chat instead (host null).
                    devDocked && "hidden",
                  )}
                  data-testid="artifact-canvas-host"
                />
              </div>
            )}
            {/* Docked Code: a visible seam between chat and the display
                panel. Drag it to resize the right column. Hidden when the
                chat is maximized, because the rail has a fixed width. */}
            {devDocked && !chatMaximized && (
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label={t("dev.resizeCodePanel", { defaultValue: "Resize Code panel" })}
                data-testid="dev-code-separator"
                onPointerDown={startDevPanelDrag}
                className="absolute inset-y-0 z-20 w-1.5 cursor-col-resize bg-border/70 transition-colors hover:bg-primary/50"
                style={{ right: devPanelEffective, bottom: DEV_STATUS_HEIGHT }}
              />
            )}
            {isWorkbenchView(deskView) &&
              !isNarrowWorkbench &&
              !hideSideChat &&
              !devDocked && (
              <div
                role="separator"
                aria-orientation="vertical"
                onPointerDown={startDevChatDrag}
                className="absolute inset-y-0 z-20 w-1.5 cursor-col-resize transition-colors hover:bg-primary/40"
                style={{ right: devChatWidth }}
              />
            )}
            <div
              className={cn(
                "absolute flex flex-col",
                isWorkbenchView(deskView) && isNarrowWorkbench
                  ? "inset-x-0 bottom-0 z-40 h-[min(78vh,43rem)] overflow-hidden rounded-t-2xl border border-border/55 bg-background shadow-2xl"
                  : isWorkbenchView(deskView)
                    ? cn(
                        "inset-y-0 overflow-hidden",
                        // Undocked: chat is the right column. Docked display
                        // mode keeps the seam on the resize handle, not here.
                        !chatDocked && "right-0 border-l border-border/55",
                      )
                    : "inset-0",
                view !== "chat" && !isWorkbenchView(deskView) && "invisible pointer-events-none",
                isWorkbenchView(deskView) && hideSideChat && "invisible pointer-events-none",
                // Docked: paints over the center the workbench leaves empty.
                chatDocked && "z-10 bg-background",
              )}
              style={
                isWorkbenchView(deskView) && !isNarrowWorkbench
                  ? chatDocked
                    ? // Code: the chat sits after the explorer column and before
                      // the docked panel. Never slide left over the tree when
                      // the host sidebar reopens and the shell shrinks.
                      (() => {
                        const explorerOffset =
                          devExplorerWidth > 0 ? devExplorerWidth : 0;
                        return {
                          left: explorerOffset,
                          right: Math.max(0, devRightWidth),
                          width: "auto" as const,
                          bottom: DEV_STATUS_HEIGHT,
                        };
                      })()
                    : { width: hideSideChat ? 0 : devChatWidth }
                  : undefined
              }
            >
              <div className="flex min-h-0 flex-1 flex-col">
              <ThreadShell
                session={activeSession}
                title={headerTitle}
                onToggleSidebar={toggleSidebar}
                onNewChat={onNewChat}
                onCreateChat={onCreateChat}
                onForkChat={onForkChat}
                onRevertResubmit={onRevertResubmit}
                onTurnEnd={onTurnEnd}
                onUserMessage={noteUserMessage}
                hideSidebarToggleForHostChrome
                hostChromeTitleInset={false}
                hideHeader={isWorkbenchView(deskView)}
                artifactCanvasHost={devDocked ? null : artifactCanvasHost}
                workspaceScope={activeWorkspaceScope}
                workspaceDefaultScope={workspaces?.default_scope ?? null}
                workspaceControls={workspaces?.controls ?? null}
                workspaceScopeDisabled={activeChatRunning}
                workspaceError={workspaceError}
                onWorkspaceScopeChange={applyWorkspaceScopeFromComposer}
                settingsSnapshot={settingsSnapshot}
                onSettingsChange={setSettingsSnapshot}
                onOpenModelSettings={onOpenModelSettings}
                onOpenVoiceSettings={onOpenVoiceSettings}
                skills={skills}
                onOpenFileInEditor={onOpenFileInDevEditor}
                workbenchVisible={isWorkbenchView(deskView)}
                autoSendRequest={
                  isWorkbenchView(deskView) ? devAutoSendRequest : chatAutoSendRequest
                }
                composerSeed={isWorkbenchView(deskView) ? devComposerSeed : null}
                onComposerSeedConsumed={onDevComposerSeedConsumed}
                productModule={productModule}
                headerLeadingActions={
                  view === "chat" && !activeKey ? (
                    <HeaderUsageIndicator onClick={onOpenAccountSettings} />
                  ) : undefined
                }
              />
              </div>
            </div>
            {view !== "chat" && !isWorkbenchView(view) && (
              <div className="absolute inset-0 z-30 flex min-h-0 flex-col overflow-hidden">
                {view !== "settings" &&
                  view !== "tools" &&
                  view !== "apps" &&
                  view !== "skills" &&
                  view !== "automations" && (
                  <UtilityNavbar
                    active={view as "apps" | "skills" | "automations" | "templates"}
                    onOpenTools={onOpenTools}
                    onOpenSkills={onOpenSkills}
                    onOpenTemplates={onOpenTemplates}
                    onOpenAutomations={onOpenAutomations}
                  />
                )}
                <Suspense
                  fallback={
                    <div className="flex flex-1 items-center justify-center bg-background text-sm text-muted-foreground">
                      {t("settings.status.loading", { defaultValue: "Loading settings" })}
                    </div>
                  }
                >
                <SettingsView
                  theme={theme}
                  initialSection={settingsInitialSection}
                  initialSettings={settingsSnapshot}
                  showSidebar={view === "settings"}
                  onToggleTheme={toggle}
                  onBackToChat={onBackToChat}
                  onModelNameChange={onModelNameChange}
                  onSettingsChange={setSettingsSnapshot}
                  skills={skills}
                  onSkillsChange={refreshSkills}
                  onWorkspaceSettingsChange={refreshWorkspaces}
                  onSectionChange={onSettingsSectionChange}
                  onRestart={onRestart}
                  onNativeEngineRestart={onNativeEngineRestart}
                  isRestarting={isRestarting}
                  hostChromeInset={showHostChrome}
                />
                </Suspense>
              </div>
            )}
          </main>
        </div>

        <DeleteConfirm
          open={!!pendingDelete}
          title={pendingDelete?.label ?? ""}
          automations={pendingDelete?.automations}
          onCancel={() => setPendingDelete(null)}
          onConfirm={onConfirmDelete}
        />
        <DeleteConfirm
          open={!!pendingProjectDelete}
          title={pendingProjectDelete?.label ?? ""}
          description={t("deleteConfirm.projectDescription")}
          onCancel={() => setPendingProjectDelete(null)}
          onConfirm={onConfirmProjectDelete}
        />
        <RenameChatDialog
          open={!!pendingRename}
          title={pendingRename?.label ?? ""}
          onCancel={() => setPendingRename(null)}
          onConfirm={onConfirmRename}
        />
        <RenameChatDialog
          open={!!pendingProjectRename}
          title={pendingProjectRename?.label ?? ""}
          dialogTitle={t("chat.renameProjectTitle")}
          description={t("chat.renameProjectDescription")}
          placeholder={t("chat.renameProjectPlaceholder")}
          onCancel={() => setPendingProjectRename(null)}
          onConfirm={onConfirmProjectRename}
        />
        <RenameChatDialog
          open={newProjectFolderOpen}
          title=""
          dialogTitle={t("chat.newProjectFolderTitle")}
          description={t("chat.newProjectFolderDescription")}
          placeholder={t("chat.newProjectFolderPlaceholder")}
          onCancel={() => setNewProjectFolderOpen(false)}
          onConfirm={(name) => void onConfirmNewProjectFolder(name)}
        />
        {restartToast ? (
          <div
            role="status"
            className="fixed left-1/2 top-[calc(0.75rem+env(safe-area-inset-top))] z-50 max-w-[calc(100vw-1rem)] -translate-x-1/2 rounded-full border border-border/70 bg-popover px-4 py-2 text-sm font-medium text-popover-foreground shadow-lg"
          >
            {restartToast}
          </div>
        ) : null}
        <ProductToastStack
          availableUpdate={availableUpdate}
          updateBusy={updateBusy}
          updateError={updateError}
          updateStatus={updateStatus}
          onInstallUpdate={() => void installAvailableUpdate()}
          onDismissUpdate={dismissAvailableUpdate}
          onSkipUpdate={() => void skipAvailableUpdate()}
          onOpenAnnouncement={(item) => void handleAnnouncementOpen(item)}
          onReload={onRestart}
          token={token}
        />
        <PairingCodePopup
          requests={visiblePairingRequests}
          total={visiblePairingRequests.length}
          busyCode={pairingBusyCode}
          error={pairingError}
          onApprove={(code) => void onPairingAction("approve", code)}
          onDismiss={onDismissPairingRequest}
        />
        <FirstRunWizard
          open={showFirstRunWizard}
          token={token}
          onComplete={() => {
            setShowFirstRunWizard(false);
            // Providers / presets may have changed during the wizard (Free
            // path stores the OpenRouter key): refresh so the banner and
            // Settings reflect the new state without a reload.
            refreshWorkspaceState();
          }}
          onFreeStageChange={setWizardFreeStage}
          onWorkspaceSynced={refreshWorkspaceState}
          onOpenAccount={() => onOpenSettings("account")}
          onOpenProviders={() => onOpenSettings("providers")}
          onOpenDemo={() => {
            onSeedDevComposer(
              "Open the bundled Navin demo workspace under navin/templates/demo and summarize what is in it.",
            );
          }}
        />
        <FreeSetupDialog
          open={freeSetupOpen}
          token={token}
          onSynced={refreshWorkspaceState}
          onClose={() => {
            setFreeSetupOpen(false);
            // The user may have finished a connection before dismissing:
            // refresh so the "no model ready" banner reflects reality.
            refreshWorkspaceState();
          }}
          onDone={() => {
            setFreeSetupOpen(false);
            refreshWorkspaceState();
          }}
        />
        <ZoomIndicator
          zoom={uiZoom.zoom}
          visible={uiZoom.visible}
          onZoomIn={uiZoom.zoomIn}
          onZoomOut={uiZoom.zoomOut}
          onReset={uiZoom.reset}
        />
      </div>
    </ThemeProvider>
  );
}

function UtilityNavbar({
  active,
  onOpenTools,
  onOpenSkills,
  onOpenTemplates,
  onOpenAutomations,
  onOpenSearch,
}: {
  active: "apps" | "skills" | "automations" | "templates" | null;
  onOpenTools: () => void;
  onOpenSkills: () => void;
  onOpenTemplates: () => void;
  onOpenAutomations: () => void;
  onOpenSearch?: () => void;
}) {
  const { t } = useTranslation();
  const items = [
    {
      id: "tools" as const,
      icon: Wrench,
      label: t("sidebar.tools", { defaultValue: "Tools & Mcp" }),
      onClick: onOpenTools,
    },
    {
      id: "skills" as const,
      icon: Brain,
      label: t("sidebar.skills.title", { defaultValue: "Skills" }),
      onClick: onOpenSkills,
    },
    {
      id: "templates" as const,
      icon: LayoutTemplate,
      label: t("sidebar.templates", { defaultValue: "Templates" }),
      onClick: onOpenTemplates,
    },
    {
      id: "automations" as const,
      icon: Repeat,
      label: t("sidebar.automations", { defaultValue: "Loop" }),
      onClick: onOpenAutomations,
    },
    ...(onOpenSearch
      ? [
          {
            id: "search" as const,
            icon: Search,
            label: t("sidebar.searchAria", { defaultValue: "Search" }),
            onClick: onOpenSearch,
          },
        ]
      : []),
  ];
  return (
    <div className="flex h-11 shrink-0 items-center gap-1 overflow-x-auto border-b border-border/55 bg-muted/15 px-2">
      {items.map(({ id, icon: Icon, label, onClick }) => (
        <button
          key={id}
          type="button"
          onClick={onClick}
          aria-current={active === id ? "page" : undefined}
          className={cn(
            "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
            active === id
              ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
              : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
          )}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden />
          {label}
        </button>
      ))}
    </div>
  );
}
