// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cloneElement, useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowDown, Bell, BellOff, ChevronUp, Download, Globe2, Hand, Loader2, Lock, Moon, MoreHorizontal, PanelLeft, Radio, RefreshCw, Send, ShieldCheck, SquareTerminal, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ChoicePrompt } from "@/components/thread/ChoicePrompt";
import { ThreadMessages } from "@/components/thread/ThreadMessages";
import { userPromptAnchors } from "@/components/thread/promptNavigation";
import { useNavinStream } from "@/hooks/useNavinStream";
import { NavinClient } from "@/lib/navin-client";
import type { UIMessage, WebuiThreadPagePayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ClientProvider, useClient } from "@/providers/ClientProvider";
import { applyComposerTurnMode } from "@/components/thread/ComposerModeMenu";
import { SubagentChatView, SubagentsButton, SubagentsSidePanel } from "@/components/thread/SubagentsSidePanel";

import { GuestMedia, withGuestMedia } from "./guest-media";
import { useGuestSubagents } from "./useGuestSubagents";
// The app icon itself, as in the Dock and the taskbar.
import navinIcon from "./navin-icon.png";
import { GuestSiteTunnel, type SiteFailure } from "./guest-site";
import { GuestFilesClient } from "./guest-files";
import { guestNotify, notificationsEnabled, notificationsSupported, toggleNotifications } from "./guest-notify";
import { GuestControlBar, useGuestMode } from "./GuestControlBar";
import { GuestLivePanel, SiteView, type LiveTab } from "./GuestLivePanel";
import { GuestSidebar } from "./GuestSidebar";
import { useGuestTheme } from "./guest-theme";
import { JOIN_STAGE_PERCENT, RelaySession, type ShareContext, type ShareSnapshot } from "./relay-socket";
import { ShareCipher, parseShareLocation } from "./share-crypto";

const NAME_KEY = "navin.guest.name";

type Rights = { drive: boolean; download: boolean };

interface ThreadPage {
  messages: UIMessage[];
  pending: boolean;
  before: string | null;
  hasMore: boolean;
  userOffset: number;
}

function threadPage(thread: unknown, idPrefix: string): ThreadPage {
  const body = thread as {
    messages?: UIMessage[];
    has_pending_tool_calls?: boolean;
    page?: WebuiThreadPagePayload;
  } | null;
  const messages = (body?.messages ?? []).map((m, idx) => ({
    ...m,
    id: m.id ?? `${idPrefix}-${idx}`,
    createdAt: typeof m.createdAt === "number" ? m.createdAt : Date.now(),
  }));
  return {
    messages,
    pending: Boolean(body?.has_pending_tool_calls),
    before: body?.page?.before_cursor ?? null,
    hasMore: Boolean(body?.page?.has_more_before),
    userOffset: body?.page?.user_message_offset ?? 0,
  };
}

type OlderLoader = (before: string) => Promise<unknown>;

function relaySocketUrl(shareId: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/guest/${shareId}`;
}

export function GuestApp() {
  const { t } = useTranslation();
  const link = useMemo(() => parseShareLocation(window.location), []);
  // An app link (?app) opens the app at once: a visitor has nothing to sign.
  const appLink = useMemo(() => new URLSearchParams(window.location.search).has("app"), []);
  const [name, setName] = useState(() => localStorage.getItem(NAME_KEY) ?? "");
  const [joined, setJoined] = useState(appLink);

  // Warm up while the visitor types their name: the thread renders at once.
  useEffect(() => {
    void import("@/components/MarkdownTextRenderer").catch(() => undefined);
  }, []);

  if (!link) {
    return (
      <Centered>
        <Lock className="h-6 w-6 text-muted-foreground" aria-hidden />
        <p className="text-sm text-muted-foreground">
          {t("chat.liveShare.guest.badLink", { defaultValue: "This share link is incomplete. Ask the host for the full link." })}
        </p>
      </Centered>
    );
  }

  if (!joined) {
    const submit = (e: FormEvent) => {
      e.preventDefault();
      const clean = name.trim().slice(0, 40);
      if (!clean) return;
      localStorage.setItem(NAME_KEY, clean);
      setName(clean);
      setJoined(true);
    };
    return (
      <Centered>
        <motion.form
          onSubmit={submit}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", duration: 0.35, bounce: 0 }}
          className="w-full max-w-sm space-y-4 rounded-xl border border-border/70 bg-card p-6 text-start shadow-sm"
        >
          <div className="flex items-center gap-2 text-base font-semibold">
            <Radio className="h-4 w-4 text-primary" aria-hidden />
            {t("chat.liveShare.guest.joinTitle", { defaultValue: "Join a shared Navin session" })}
          </div>
          <p className="text-[13px] text-muted-foreground">
            {t("chat.liveShare.guest.joinHint", {
              defaultValue: "You follow the session live, read only. The host can let you drive the agent or download the session.",
            })}
          </p>
          <label className="block space-y-1.5 text-[13px]">
            <span className="font-medium">{t("chat.liveShare.guest.name", { defaultValue: "Your name" })}</span>
            <input
              autoFocus
              value={name}
              maxLength={40}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
          <Button type="submit" className="w-full" disabled={!name.trim()}>
            {t("chat.liveShare.guest.join", { defaultValue: "Join" })}
          </Button>
          <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
            {t("chat.liveShare.guest.e2e", { defaultValue: "End-to-end encrypted: the relay never sees the content." })}
          </p>
        </motion.form>
      </Centered>
    );
  }

  const visitor = name.trim() || t("chat.liveShare.guest.app.visitor", { defaultValue: "Visitor" });
  return <GuestSession shareId={link.shareId} shareKey={link.key} name={appLink ? visitor : name} appLink={appLink} />;
}

function GuestSession({
  shareId,
  shareKey,
  name,
  appLink = false,
}: {
  shareId: string;
  shareKey: string;
  name: string;
  appLink?: boolean;
}) {
  const { t } = useTranslation();
  const [client, setClient] = useState<NavinClient | null>(null);
  const { theme, toggle: toggleTheme } = useGuestTheme();
  const [session, setSession] = useState<RelaySession | null>(null);
  const [snapshot, setSnapshot] = useState<ShareSnapshot | null>(null);
  const [rights, setRights] = useState<Rights>({ drive: false, download: false });
  const [hostOnline, setHostOnline] = useState(true);
  // Every seat is taken by a socket about to time out: the relay said so
  // with a retry, the client dials again by itself. Shown, never final.
  const [roomFull, setRoomFull] = useState(false);
  // Join progress from real steps; never moves back on a reconnect.
  const [joinPercent, setJoinPercent] = useState(0);
  const [closed, setClosed] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [requested, setRequested] = useState<{ drive?: boolean; download?: boolean }>({});
  // Read inside the relay callback, which outlives a render.
  const requestedRef = useRef<{ drive?: boolean; download?: boolean }>({});
  const [bundle, setBundle] = useState<{ received: number; total: number } | null>(null);
  const [context, setContext] = useState<ShareContext | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.matchMedia?.("(min-width: 900px)").matches ?? true);
  const chunks = useRef<string[]>([]);
  const historyWaiter = useRef<((thread: unknown) => void) | null>(null);
  const relayRef = useRef<RelaySession | null>(null);
  const media = useMemo(
    () => new GuestMedia((payload) => void relayRef.current?.request("media", payload)),
    [],
  );
  const [notifyOn, setNotifyOn] = useState(notificationsEnabled);
  const [liveOpen, setLiveOpen] = useState(false);
  // Which header button glows: new activity in a tab the guest is not watching.
  const [livePulse, setLivePulse] = useState<{ terminal: boolean; browser: boolean }>({ terminal: false, browser: false });
  const [liveTab, setLiveTab] = useState<LiveTab>("terminal");
  const [liveTabRequest, setLiveTabRequest] = useState<{ tab: "terminal" | "browser"; n: number }>({ tab: "terminal", n: 0 });
  const [siteOn, setSiteOn] = useState(false);
  const [siteFocus, setSiteFocus] = useState(0);
  const siteTunnel = useMemo(
    () => new GuestSiteTunnel(
      (requests) => void relayRef.current?.request("site", { requests }),
      (failure) => siteFailureText(failure, t),
    ),
    [t],
  );
  useEffect(() => () => siteTunnel.dispose(), [siteTunnel]);
  const filesClient = useMemo(
    () => new GuestFilesClient((type, extra) => void relayRef.current?.request(type, extra)),
    [],
  );
  const isWide = () => window.matchMedia?.("(min-width: 900px)").matches ?? true;
  // The host shares its site: beside the chat on wide screens, a pulse on phones.
  const showSite = useCallback(() => {
    setSiteFocus((n) => n + 1);
    if (window.matchMedia?.("(min-width: 1100px)").matches) setLiveOpen(true);
    else setLivePulse((prev) => ({ ...prev, browser: true }));
  }, []);

  useEffect(() => {
    let disposed = false;
    let navin: NavinClient | null = null;
    void (async () => {
      let cipher: ShareCipher;
      try {
        cipher = await ShareCipher.create(shareKey, shareId);
      } catch {
        setClosed(t("chat.liveShare.guest.badLink", { defaultValue: "This share link is incomplete. Ask the host for the full link." }));
        return;
      }
      if (disposed) return;
      setJoinPercent((prev) => Math.max(prev, 10));
      const relay = new RelaySession(relaySocketUrl(shareId), cipher, name, {
        onJoinStage: (stage) => setJoinPercent((prev) => Math.max(prev, JOIN_STAGE_PERCENT[stage])),
        onSnapshot: (snap) => {
          setSnapshot((prev) => prev ?? snap);
          setRights(snap.rights);
          if (snap.context) setContext(snap.context);
          setSiteOn((was) => {
            if (snap.site && !was) showSite();
            return Boolean(snap.site);
          });
        },
        onHostOnline: (online) => {
          setHostOnline(online);
          if (!online) {
            media.reset();
            siteTunnel.failAll("unreachable");
            guestNotify(
              t("chat.liveShare.guest.hostAway", { defaultValue: "The host is offline. Waiting for them..." }),
              "",
              "navin-host",
            );
          }
        },
        onRoomFull: setRoomFull,
        onClosed: (reason) => {
          setClosed(reason);
          guestNotify(t("chat.liveShare.guest.ended", { defaultValue: "This share has ended." }), reason, "navin-closed");
          navin?.close();
        },
        onPayload: (payload) => {
          if (payload.type === "media_chunk" || payload.type === "media_error") {
            media.handle(payload);
          } else if (payload.type === "files" || payload.type === "file_chunk" || payload.type === "file_error") {
            filesClient.handle(payload);
          } else if (payload.type === "site_res") {
            siteTunnel.handle(payload);
          } else if (payload.type === "site") {
            const enabled = Boolean(payload.enabled);
            setSiteOn(enabled);
            if (enabled) {
              showSite();
              guestNotify(t("chat.liveShare.guest.site.shared", { defaultValue: "The host shares the running site" }), "", "navin-site");
            } else {
              siteTunnel.failAll("off");
            }
          } else if (payload.type === "context") {
            setContext((payload.context as ShareContext | null) ?? null);
          } else if (payload.type === "history") {
            historyWaiter.current?.(payload.thread);
            historyWaiter.current = null;
          } else if (payload.type === "rights") {
            const next = { drive: Boolean(payload.drive), download: Boolean(payload.download) };
            // Asked, then granted: the download starts now, no second click.
            if (next.download && requestedRef.current.download) {
              void relayRef.current?.request("request_download");
            }
            setRights((prev) => {
              if (next.drive && !prev.drive) {
                setNotice("granted_drive");
                guestNotify(t("chat.liveShare.guest.granted.drive", { defaultValue: "You can now drive the agent" }), "", "navin-rights");
              } else if (next.download && !prev.download) {
                setNotice("granted_download");
                guestNotify(t("chat.liveShare.guest.granted.download", { defaultValue: "You can now download the session" }), "", "navin-rights");
              }
              return next;
            });
            setRequested({});
          } else if (payload.type === "refused") {
            setNotice(String(payload.reason || "refused"));
          } else if (payload.type === "bundle_start") {
            chunks.current = [];
            setBundle({ received: 0, total: 0 });
          } else if (payload.type === "bundle_chunk") {
            const total = Number(payload.total) || 1;
            chunks.current[Number(payload.index) || 0] = String(payload.data || "");
            const received = chunks.current.filter(Boolean).length;
            setBundle({ received, total });
            if (received === total) {
              saveBundle(chunks.current, String(payload.name || "session.navin-session"));
              chunks.current = [];
              setBundle(null);
            }
          } else if (payload.type === "bundle_error") {
            setBundle(null);
            setNotice(String(payload.reason || "bundle_error"));
          }
        },
      });
      navin = new NavinClient({ url: relaySocketUrl(shareId), socketFactory: relay.socketFactory, maxBackoffMs: 10_000 });
      navin.connect();
      relayRef.current = relay;
      setSession(relay);
      setClient(navin);
    })();
    return () => {
      disposed = true;
      navin?.close();
    };
  }, [filesClient, media, name, shareId, shareKey, showSite, siteTunnel, t]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const loadOlder = useCallback<OlderLoader>(
    (before) =>
      new Promise((resolve) => {
        historyWaiter.current?.(null);
        historyWaiter.current = resolve;
        void session?.request("history", { before });
        // The host may be away: give up rather than hang the button.
        window.setTimeout(() => {
          if (historyWaiter.current === resolve) {
            historyWaiter.current = null;
            resolve(null);
          }
        }, 15_000);
      }),
    [session],
  );

  const ask = (what: "drive" | "download") => {
    requestedRef.current = { ...requestedRef.current, [what]: true };
    setRequested((prev) => ({ ...prev, [what]: true }));
    void session?.request(what === "drive" ? "request_control" : "request_download");
  };

  const downloadLabel = bundle
    ? t("chat.liveShare.guest.downloading", {
        defaultValue: "Downloading {{done}}/{{total}}",
        done: bundle.received,
        total: bundle.total || "?",
      })
    : rights.download
      ? t("chat.liveShare.guest.download", { defaultValue: "Download the session" })
      : requested.download
        ? t("chat.liveShare.guest.asked", { defaultValue: "Request sent" })
        : t("chat.liveShare.guest.askDownload", { defaultValue: "Ask to download" });

  if (closed) {
    return (
      <Centered>
        <Lock className="h-6 w-6 text-muted-foreground" aria-hidden />
        <p className="text-sm font-medium">{t("chat.liveShare.guest.ended", { defaultValue: "This share has ended." })}</p>
        <p className="text-[13px] text-muted-foreground">{closedReasonText(closed, t)}</p>
        {/* A link the host did not revoke may well be alive again (relay
            restarted, host back): one tap tries it, the page is not a dead end. */}
        <Button size="sm" variant="outline" className="mt-1 gap-1.5" onClick={() => window.location.reload()}>
          <RefreshCw className="h-4 w-4" aria-hidden />
          {t("chat.liveShare.guest.retry", { defaultValue: "Try again" })}
        </Button>
      </Centered>
    );
  }

  if (!client || !snapshot) {
    const stageLabel = roomFull
      ? t("chat.liveShare.guest.full", { defaultValue: "Every seat is taken right now. Retrying..." })
      : !hostOnline
        ? t("chat.liveShare.guest.hostAway", { defaultValue: "The host is offline. Waiting for them..." })
        : joinPercent >= JOIN_STAGE_PERCENT.data
          ? t("chat.liveShare.guest.progress.loading", { defaultValue: "Loading the conversation..." })
          : joinPercent >= JOIN_STAGE_PERCENT.hello
            ? t("chat.liveShare.guest.progress.host", { defaultValue: "Waiting for the host's reply..." })
            : joinPercent >= JOIN_STAGE_PERCENT.socket
              ? t("chat.liveShare.guest.progress.relay", { defaultValue: "Connecting to the share..." })
              : appLink
                ? t("chat.liveShare.guest.app.opening", { defaultValue: "Opening the app..." })
                : t("chat.liveShare.guest.joining", { defaultValue: "Joining the session..." });
    return (
      <Centered>
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
        <div className="w-full max-w-[260px]">
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={joinPercent}
            aria-label={stageLabel}
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
              style={{ width: `${joinPercent}%` }}
            />
          </div>
          <p className="mt-2 text-[12px] tabular-nums text-muted-foreground">{joinPercent}%</p>
        </div>
        <p className="text-sm text-muted-foreground">{stageLabel}</p>
      </Centered>
    );
  }

  if (snapshot.scope === "site") {
    return (
      <div className="flex h-[100dvh] flex-col">
        <header
          className="flex items-center gap-2 border-b border-border/70 px-3 py-1.5 sm:px-4"
          style={{ paddingTop: "max(0.375rem, env(safe-area-inset-top))" }}
        >
          {/* An app link shows the app and nothing else: no session state,
              rights or controls. Only an offline host is worth a word. */}
          <img src={navinIcon} alt="" className="h-6 w-6 shrink-0 rounded-md" draggable={false} />
          <span className="min-w-0 flex-1 truncate text-sm font-medium">
            {t("chat.liveShare.guest.app.title", { defaultValue: "Application" })}
          </span>
          {!hostOnline ? (
            <span className="flex shrink-0 items-center gap-1.5 text-[12px] text-muted-foreground">
              <span className="h-2 w-2 rounded-full bg-muted-foreground/40" />
              {t("chat.liveShare.guest.app.away", { defaultValue: "Paused: the host's computer is offline" })}
            </span>
          ) : null}
        </header>
        {siteOn ? (
          <SiteView tunnel={siteTunnel} hidden={false} />
        ) : (
          <Centered>
            <p className="text-sm text-muted-foreground">
              {t("chat.liveShare.guest.site.off", { defaultValue: "The host stopped sharing the site." })}
            </p>
          </Centered>
        )}
      </div>
    );
  }

  return (
    <ClientProvider client={client} token="">
      <div className="flex h-[100dvh] flex-col">
        <header
          className="flex items-center gap-1.5 border-b border-border/70 px-2 py-2 sm:gap-3 sm:px-4 sm:py-2.5"
          style={{ paddingTop: "max(0.5rem, env(safe-area-inset-top))" }}
        >
          <span className="flex shrink-0 items-center gap-2 pe-1">
            <img src={navinIcon} alt="" className="h-7 w-7 rounded-lg shadow-sm" draggable={false} />
            <span className="text-[15px] font-semibold tracking-tight max-sm:sr-only">Navin</span>
          </span>
          <Button
            size="icon"
            variant="ghost"
            className="h-9 w-9 shrink-0"
            aria-pressed={sidebarOpen}
            aria-label={t("chat.liveShare.guest.ctx.toggle", { defaultValue: "Session details" })}
            onClick={() => setSidebarOpen((v) => !v)}
          >
            <PanelLeft className="rtl:-scale-x-100 h-4 w-4" aria-hidden />
          </Button>
          <div className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium">
            <span className={cn("h-2 w-2 shrink-0 rounded-full", hostOnline ? "bg-emerald-500" : "bg-muted-foreground/40")} />
            <span className="truncate">
              {hostOnline
                ? t("chat.liveShare.guest.live", { defaultValue: "Live session" })
                : t("chat.liveShare.guest.hostAway", { defaultValue: "The host is offline. Waiting for them..." })}
            </span>
            <span className="shrink-0 whitespace-nowrap rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
              {rights.drive
                ? t("chat.liveShare.guest.driving", { defaultValue: "You can drive" })
                : t("chat.liveShare.guest.readOnly", { defaultValue: "Read only" })}
            </span>
          </div>
          {(["terminal", "browser"] as const).map((kind) => {
            const active = liveOpen && liveTab === kind;
            const Icon = kind === "terminal" ? SquareTerminal : Globe2;
            const label = kind === "terminal"
              ? t("chat.liveShare.guest.livePanel.terminal", { defaultValue: "Terminal" })
              : t("chat.liveShare.guest.livePanel.browser", { defaultValue: "Browser" });
            return (
              <Button
                key={kind}
                size="sm"
                variant={active ? "secondary" : "ghost"}
                className="relative h-9 shrink-0 gap-1.5 max-sm:w-9 max-sm:px-0"
                aria-pressed={active}
                aria-label={label}
                title={label}
                onClick={() => {
                  if (active) {
                    setLiveOpen(false);
                  } else {
                    setLiveOpen(true);
                    setLiveTabRequest((prev) => ({ tab: kind, n: prev.n + 1 }));
                  }
                  setLivePulse((prev) => ({ ...prev, [kind]: false }));
                }}
              >
                <Icon className="h-4 w-4" aria-hidden />
                <span className="max-sm:sr-only">{label}</span>
                {livePulse[kind] && !active ? (
                  <span className="absolute end-1 top-1 h-2 w-2 animate-pulse rounded-full bg-sky-500" />
                ) : null}
              </Button>
            );
          })}
          <Button
            size="icon"
            variant="ghost"
            className="hidden h-9 w-9 shrink-0 sm:inline-flex"
            onClick={toggleTheme}
            aria-label={theme === "dark"
              ? t("chat.liveShare.guest.themeLight", { defaultValue: "Day mode" })
              : t("chat.liveShare.guest.themeDark", { defaultValue: "Night mode" })}
            title={theme === "dark"
              ? t("chat.liveShare.guest.themeLight", { defaultValue: "Day mode" })
              : t("chat.liveShare.guest.themeDark", { defaultValue: "Night mode" })}
          >
            {theme === "dark" ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}
          </Button>
          {notificationsSupported() ? (
            <Button
              size="icon"
              variant="ghost"
              className="hidden h-9 w-9 shrink-0 sm:inline-flex"
              aria-pressed={notifyOn}
              aria-label={
                notifyOn
                  ? t("chat.liveShare.guest.notifyOff", { defaultValue: "Turn notifications off" })
                  : t("chat.liveShare.guest.notifyOn", { defaultValue: "Notify me (replies, questions, rights)" })
              }
              title={
                notifyOn
                  ? t("chat.liveShare.guest.notifyOff", { defaultValue: "Turn notifications off" })
                  : t("chat.liveShare.guest.notifyOn", { defaultValue: "Notify me (replies, questions, rights)" })
              }
              onClick={() => void toggleNotifications().then(setNotifyOn)}
            >
              {notifyOn ? <Bell className="h-4 w-4" aria-hidden /> : <BellOff className="h-4 w-4" aria-hidden />}
            </Button>
          ) : null}
          {!rights.drive ? (
            <Button
              size="sm"
              variant="outline"
              className="hidden h-9 shrink-0 sm:inline-flex"
              disabled={requested.drive}
              onClick={() => ask("drive")}
              aria-label={t("chat.liveShare.guest.askControl", { defaultValue: "Ask to drive" })}
            >
              <Hand className="h-4 w-4" aria-hidden />
              <span>
                {requested.drive
                  ? t("chat.liveShare.guest.asked", { defaultValue: "Request sent" })
                  : t("chat.liveShare.guest.askControl", { defaultValue: "Ask to drive" })}
              </span>
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            className="hidden h-9 shrink-0 sm:inline-flex"
            disabled={Boolean(bundle) || (!rights.download && requested.download)}
            onClick={() => ask("download")}
            aria-label={t("chat.liveShare.guest.download", { defaultValue: "Download the session" })}
          >
            {bundle ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
            <span>{downloadLabel}</span>
          </Button>
          {/* On a phone the nine header controls overflowed and the useful ones
              (the live tabs, the session state) were pushed off screen. The
              secondary actions sit in one menu there; the desktop keeps them inline. */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                size="icon"
                variant="ghost"
                className="h-9 w-9 shrink-0 sm:hidden"
                aria-label={t("chat.liveShare.guest.more", { defaultValue: "More actions" })}
              >
                {bundle ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <MoreHorizontal className="h-4 w-4" aria-hidden />}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[220px]">
              {!rights.drive ? (
                <DropdownMenuItem disabled={requested.drive} onSelect={() => ask("drive")}>
                  <Hand className="me-2 h-4 w-4" aria-hidden />
                  {requested.drive
                    ? t("chat.liveShare.guest.asked", { defaultValue: "Request sent" })
                    : t("chat.liveShare.guest.askControl", { defaultValue: "Ask to drive" })}
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuItem
                disabled={Boolean(bundle) || (!rights.download && requested.download)}
                onSelect={() => ask("download")}
              >
                <Download className="me-2 h-4 w-4" aria-hidden />
                {downloadLabel}
              </DropdownMenuItem>
              {notificationsSupported() ? (
                <DropdownMenuItem onSelect={() => void toggleNotifications().then(setNotifyOn)}>
                  {notifyOn ? <Bell className="me-2 h-4 w-4" aria-hidden /> : <BellOff className="me-2 h-4 w-4" aria-hidden />}
                  {notifyOn
                    ? t("chat.liveShare.guest.notifyOff", { defaultValue: "Turn notifications off" })
                    : t("chat.liveShare.guest.notifyOn", { defaultValue: "Notify me (replies, questions, rights)" })}
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuItem onSelect={toggleTheme}>
                {theme === "dark" ? <Sun className="me-2 h-4 w-4" aria-hidden /> : <Moon className="me-2 h-4 w-4" aria-hidden />}
                {theme === "dark"
                  ? t("chat.liveShare.guest.themeLight", { defaultValue: "Day mode" })
                  : t("chat.liveShare.guest.themeDark", { defaultValue: "Night mode" })}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>
        <GuestThread
          chatId={snapshot.chat_id}
          thread={snapshot.thread}
          canDrive={rights.drive}
          usage={context?.usage ?? null}
          media={media}
          loadOlder={loadOlder}
          onJumped={() => {
            if (!isWide()) setSidebarOpen(false);
          }}
          onCloseSidebar={() => setSidebarOpen(false)}
          live={
            <GuestLivePanel
              client={client}
              chatId={snapshot.chat_id}
              open={liveOpen}
              site={siteOn}
              siteTunnel={siteTunnel}
              canDownload={rights.download}
              filesSource={filesClient}
              siteFocus={siteFocus}
              onClose={() => setLiveOpen(false)}
              tabRequest={liveTabRequest}
              onTabChange={setLiveTab}
              onActivity={(activity) => {
                // Wide screens open it beside the chat; phones get a pulse, never a takeover.
                if (window.matchMedia?.("(min-width: 1100px)").matches) setLiveOpen(true);
                else setLivePulse((prev) => ({
                  terminal: prev.terminal || activity.terminal,
                  browser: prev.browser || activity.browser,
                }));
              }}
              className="w-[46%] min-w-[340px] shrink-0 max-[1099px]:absolute max-[1099px]:inset-0 max-[1099px]:z-30 max-[1099px]:w-full max-[1099px]:min-w-0"
            />
          }
          sidebar={
            sidebarOpen ? (
              <GuestSidebar
                context={context}
                expiresAt={snapshot.expires_at}
                onJump={() => undefined}
                className="w-[300px] shrink-0 max-[899px]:absolute max-[899px]:inset-y-0 max-[899px]:start-0 max-[899px]:z-30 max-[899px]:w-[min(86vw,340px)] max-[899px]:bg-background max-[899px]:shadow-xl"
              />
            ) : null
          }
        />
        <AnimatePresence>
          {notice ? (
            <motion.div
              role="status"
              // framer-motion owns transform: centring goes through x, not a class.
              initial={{ opacity: 0, y: 8, x: "-50%" }}
              animate={{ opacity: 1, y: 0, x: "-50%" }}
              exit={{ opacity: 0, y: 8, x: "-50%" }}
              className="fixed bottom-24 left-1/2 z-40 w-max max-w-[90vw] rounded-md border border-border bg-card px-3 py-2 text-center text-[13px] shadow-md"
            >
              {refusalText(notice, t)}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </ClientProvider>
  );
}

function siteFailureText(failure: SiteFailure, t: ReturnType<typeof useTranslation>["t"]): string {
  switch (failure) {
    case "off":
      return t("chat.liveShare.guest.site.off", { defaultValue: "The host stopped sharing the site." });
    case "too_large":
      return t("chat.liveShare.guest.site.tooLarge", { defaultValue: "This file is too large to open through the share." });
    case "slow_down":
      return t("chat.liveShare.guest.site.slowDown", { defaultValue: "Too many requests at once. Reload in a moment." });
    case "timeout":
      return t("chat.liveShare.guest.site.timeout", { defaultValue: "The site took too long to answer. Reload to try again." });
    default:
      return t("chat.liveShare.guest.site.unreachable", { defaultValue: "The site does not answer on the host's machine. Is it still running?" });
  }
}

function refusalText(reason: string, t: ReturnType<typeof useTranslation>["t"]): string {
  const known: Record<string, string> = {
    read_only: t("chat.liveShare.guest.refused.readOnly", { defaultValue: "Read only: ask the host to let you drive." }),
    command_not_allowed: t("chat.liveShare.guest.refused.command", { defaultValue: "This command stays with the host." }),
    not_allowed_for_guests: t("chat.liveShare.guest.refused.generic", { defaultValue: "Only the host can do this." }),
    too_long: t("chat.liveShare.guest.refused.tooLong", { defaultValue: "This message is too long (20,000 characters max)." }),
    slow_down: t("chat.liveShare.guest.refused.slowDown", { defaultValue: "Too many messages at once: wait a second." }),
    too_large: t("chat.liveShare.guest.refused.tooLarge", { defaultValue: "This session is too heavy to download in a browser: ask the host to send you the exported file." }),
    granted_drive: t("chat.liveShare.guest.granted.drive", { defaultValue: "You can now drive the agent" }),
    granted_download: t("chat.liveShare.guest.granted.download", { defaultValue: "You can now download the session" }),
  };
  return known[reason] ?? reason;
}

/** The relay's final close reasons, in the guest's language. */
function closedReasonText(reason: string, t: ReturnType<typeof useTranslation>["t"]): string {
  const known: Record<string, string> = {
    "this link is not active": t("chat.liveShare.guest.closed.inactive", { defaultValue: "The host revoked this link or it expired." }),
    revoked: t("chat.liveShare.guest.closed.revoked", { defaultValue: "The host stopped sharing." }),
    removed: t("chat.liveShare.guest.closed.removed", { defaultValue: "The host removed you from this session." }),
    expired: t("chat.liveShare.guest.closed.expired", { defaultValue: "This link has expired." }),
    "too many connections": t("chat.liveShare.guest.closed.busy", { defaultValue: "Too many connections from your network. Try again in a minute." }),
  };
  return known[reason] ?? reason;
}

function GuestThread({
  chatId,
  thread,
  canDrive,
  media,
  loadOlder,
  onJumped,
  onCloseSidebar,
  live,
  sidebar,
  usage,
}: {
  chatId: string;
  thread: unknown;
  canDrive: boolean;
  usage: ShareContext["usage"];
  media: GuestMedia;
  loadOlder: OlderLoader;
  onJumped: () => void;
  onCloseSidebar: () => void;
  live: ReactElement;
  sidebar: ReactElement<{ onJump: (index: number) => void; onOpenSubagentHistory?: () => void }> | null;
}) {
  const { t } = useTranslation();
  const { client } = useClient();
  const [mode, setMode] = useGuestMode();
  // Subagents the host's agent spawns: listed on the right, read in the chat.
  const subagents = useGuestSubagents(client, chatId);
  const initial = useMemo(() => threadPage(thread, "hist"), [thread]);
  const stream = useNavinStream(chatId, initial.messages, initial.pending);
  const { setMessages } = stream;
  const [draft, setDraft] = useState("");
  const [page, setPage] = useState({ before: initial.before, hasMore: initial.hasMore, userOffset: initial.userOffset });
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [revealId, setRevealId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRef = useRef(page);
  pageRef.current = page;
  const messagesRef = useRef(stream.messages);
  messagesRef.current = stream.messages;
  const [mediaVersion, setMediaVersion] = useState(0);
  useEffect(() => media.subscribe(() => setMediaVersion(media.version)), [media]);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- mediaVersion re-resolves blobs as they land
  const shown = useMemo(() => withGuestMedia(stream.messages, media), [stream.messages, media, mediaVersion]);
  const [atBottom, setAtBottom] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const coarse = useMemo(() => window.matchMedia?.("(pointer: coarse)").matches ?? false, []);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Follow-up: a reply landed or the agent asks something while the guest looks away.
  const wasStreaming = useRef(stream.isStreaming);
  useEffect(() => {
    if (wasStreaming.current && !stream.isStreaming) {
      const last = [...stream.messages].reverse().find((m) => m.role === "assistant" && typeof m.content === "string" && m.content.trim());
      guestNotify(t("chat.liveShare.guest.notify.replied", { defaultValue: "Navin replied" }), String(last?.content ?? ""), "navin-turn");
    }
    wasStreaming.current = stream.isStreaming;
  }, [stream.isStreaming, stream.messages, t]);
  const choiceCount = useRef(stream.pendingChoices.length);
  useEffect(() => {
    if (stream.pendingChoices.length > choiceCount.current) {
      guestNotify(t("chat.liveShare.guest.notify.question", { defaultValue: "The agent asks a question" }), stream.pendingChoices.at(-1)?.question ?? "", "navin-choice");
    }
    choiceCount.current = stream.pendingChoices.length;
  }, [stream.pendingChoices, t]);
  const approvalCount = useRef(stream.pendingApprovals.length);
  useEffect(() => {
    if (stream.pendingApprovals.length > approvalCount.current) {
      guestNotify(t("chat.liveShare.guest.notify.approval", { defaultValue: "An action waits for the host's approval" }), stream.pendingApprovals.at(-1)?.action ?? "", "navin-approval");
    }
    approvalCount.current = stream.pendingApprovals.length;
  }, [stream.pendingApprovals, t]);

  /** One older page, prepended. Returns false when there is nothing more. */
  const fetchOlder = useCallback(async (): Promise<boolean> => {
    const current = pageRef.current;
    if (!current.hasMore || !current.before) return false;
    setLoadingOlder(true);
    try {
      const older = threadPage(await loadOlder(current.before), `older-${current.before}`);
      if (!older.messages.length) return false;
      const el = scrollRef.current;
      const fromBottom = el ? el.scrollHeight - el.scrollTop : 0;
      setMessages((prev) => [...older.messages, ...prev]);
      const next = { before: older.before, hasMore: older.hasMore, userOffset: older.userOffset };
      pageRef.current = next;
      setPage(next);
      // Keep the reader where they were while content lands above.
      requestAnimationFrame(() => {
        if (el) el.scrollTop = el.scrollHeight - fromBottom;
      });
      return true;
    } finally {
      setLoadingOlder(false);
    }
  }, [loadOlder, setMessages]);

  /** Sidebar history: load pages until the prompt is in, then scroll to it. */
  const jump = useCallback(async (promptIndex: number) => {
    for (let guard = 0; guard < 50; guard += 1) {
      const anchors = userPromptAnchors(messagesRef.current);
      const local = promptIndex - pageRef.current.userOffset;
      if (local >= 0 && local < anchors.length) {
        const id = anchors[local].id;
        setRevealId(id);
        onJumped();
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            const node = scrollRef.current?.querySelector(`[data-message-id="${CSS.escape(id)}"]`);
            node?.scrollIntoView({ behavior: "smooth", block: "start" });
          }),
        );
        return;
      }
      if (local >= anchors.length || !(await fetchOlder())) return;
      await new Promise((r) => requestAnimationFrame(r));
    }
  }, [fetchOlder, onJumped]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setAtBottom(bottom);
    if (bottom) setUnseen(0);
    if (el.scrollTop < 120 && !loadingOlder && pageRef.current.hasMore) void fetchOlder();
  };

  // Follow the latest activity, like the host's own view; count what lands while scrolled up.
  const lastId = useRef<string | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    const tail = stream.messages.at(-1)?.id ?? null;
    const first = lastId.current === null;
    // Only what lands at the end counts: older pages arrive above.
    const appended = tail !== lastId.current;
    lastId.current = tail;
    if (!el) return;
    if (first) {
      // Open on the latest exchange, like any chat.
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
      return;
    }
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 240) el.scrollTop = el.scrollHeight;
    else if (appended) setUnseen((n) => n + 1);
  }, [stream.messages]);

  const toBottom = () => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setUnseen(0);
  };

  // The composer grows with its text, up to a few lines.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !canDrive) return;
    stream.send(applyComposerTurnMode(text, mode));
    setDraft("");
  };

  return (
    <>
      <div className="relative flex min-h-0 flex-1">
      {sidebar ? (
        <>
          <button
            type="button"
            aria-label={t("common.close", { defaultValue: "Close" })}
            className="absolute inset-0 z-20 bg-black/40 min-[900px]:hidden"
            onClick={onCloseSidebar}
          />
          {cloneElement(sidebar, {
            onJump: (index: number) => { subagents.back(); void jump(index); },
            onOpenSubagentHistory: () => { subagents.openHistory(); onCloseSidebar(); },
          })}
        </>
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex shrink-0 justify-end px-3"><SubagentsButton store={subagents.store} hasHistory={subagents.hasHistory} open={subagents.open} onClick={subagents.toggle} /></div>
      {/* The agent being read covers the chat without unmounting it: back finds it where it was. */}
      <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="mx-auto w-full max-w-3xl px-3 py-4 sm:px-4 sm:py-6">
          {page.hasMore ? (
            <div className="mb-4 flex justify-center">
              <Button size="sm" variant="ghost" disabled={loadingOlder} onClick={() => void fetchOlder()}>
                {loadingOlder ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <ChevronUp className="h-4 w-4" aria-hidden />}
                {t("chat.liveShare.guest.older", { defaultValue: "Earlier messages" })}
              </Button>
            </div>
          ) : null}
          <ThreadMessages messages={shown} isStreaming={stream.isStreaming} scrollParentRef={scrollRef} revealPromptId={revealId} />
          {stream.pendingApprovals.map((approval) => (
            <div key={approval.requestId} className="mt-3 rounded-lg border border-border/70 px-3 py-2 text-[13px]">
              <span className="font-medium">{approval.action || approval.tool}</span>
              <span className="ms-2 text-muted-foreground">
                {t("chat.liveShare.guest.approvalHost", { defaultValue: "waiting for the host's approval" })}
              </span>
            </div>
          ))}
          {canDrive
            ? stream.pendingChoices.map((choice) => (
                <div key={choice.requestId} className="mt-3">
                  <ChoicePrompt request={choice} onRespond={stream.respondToChoice} />
                </div>
              ))
            : null}
        </div>
      </div>
      <AnimatePresence>
        {subagents.viewing ? (
          <SubagentChatView
            key={subagents.viewing.task_id}
            client={client}
            chatId={chatId}
            agent={subagents.viewing}
            canManage={canDrive}
            onBack={subagents.back}
          />
        ) : null}
      </AnimatePresence>
      </div>
      <AnimatePresence>
        {!atBottom && unseen > 0 && !subagents.viewing ? (
          <motion.button
            type="button"
            initial={{ opacity: 0, y: 6, x: "-50%" }}
            animate={{ opacity: 1, y: 0, x: "-50%" }}
            exit={{ opacity: 0, y: 6, x: "-50%" }}
            onClick={toBottom}
            className="absolute bottom-24 left-1/2 z-10 flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-[12.5px] shadow-md"
          >
            <ArrowDown className="h-3.5 w-3.5" aria-hidden />
            {t("chat.liveShare.guest.newMessages", { defaultValue: "{{count}} new", count: unseen })}
          </motion.button>
        ) : null}
      </AnimatePresence>
      <form
        onSubmit={submit}
        className="border-t border-border/70 px-3 py-2.5 sm:px-4 sm:py-3"
        style={{ paddingBottom: "max(0.625rem, env(safe-area-inset-bottom))" }}
      >
        <GuestControlBar
          usage={usage}
          canDrive={canDrive}
          streaming={stream.isStreaming}
          mode={mode}
          onMode={setMode}
          onStop={() => stream.stop()}
        />
        <div className="mx-auto flex w-full max-w-3xl items-end gap-2">
          <textarea
            ref={textareaRef}
            value={draft}
            disabled={!canDrive}
            rows={1}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // On touch keyboards Enter is a new line; the send button sends.
              if (e.key === "Enter" && !e.shiftKey && !coarse) submit(e);
            }}
            placeholder={
              canDrive
                ? t("chat.liveShare.guest.placeholder", { defaultValue: "Message the agent..." })
                : t("chat.liveShare.guest.placeholderReadOnly", { defaultValue: "Read only. Ask to drive to write." })
            }
            className="max-h-40 min-h-[42px] flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2 text-base outline-none sm:text-sm focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
          />
          {/* A prompt sent mid-run follows the current turn, as on the host. */}
          {!stream.isStreaming || draft.trim() ? (
            <Button
              type="submit"
              size="icon"
              className="h-[42px] w-[42px] shrink-0"
              disabled={!canDrive || !draft.trim()}
              aria-label={t("chat.liveShare.guest.send", { defaultValue: "Send" })}
            >
              <Send className="h-4 w-4" aria-hidden />
            </Button>
          ) : null}
        </div>
      </form>
      </div>
      {subagents.open ? (
        <SubagentsSidePanel
          key={chatId}
          client={client}
          chatId={chatId}
          store={subagents.store}
          includeCompleted={subagents.includeCompleted}
          canManage={canDrive}
          onSelect={subagents.select}
          selectedId={subagents.viewing?.task_id ?? null}
          onClose={subagents.close}
        />
      ) : null}
      {live}
      </div>
    </>
  );
}

function saveBundle(parts: string[], name: string): void {
  const bytes = parts.map((part) => Uint8Array.from(atob(part), (c) => c.charCodeAt(0)));
  const url = URL.createObjectURL(new Blob(bytes, { type: "application/zip" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">{children}</div>;
}
