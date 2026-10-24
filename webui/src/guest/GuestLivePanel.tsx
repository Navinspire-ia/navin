// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AppWindow, FolderOpen, Globe2, Loader2, Maximize2, Minimize2, RotateCw, SquareTerminal, X } from "lucide-react";

import { useAgentLiveSessions } from "@/hooks/useAgentLiveSessions";
import type { NavinClient } from "@/lib/navin-client";
import { cn } from "@/lib/utils";

import { GuestFiles, type GuestFilesSource } from "./GuestFiles";
import type { GuestSiteTunnel } from "./guest-site";

// xterm only loads when a command actually runs.
const AgentExecTerminal = lazy(() => import("@/components/dev/AgentExecTerminal"));

type Run = { id: string; command: string; exited: boolean; exitCode: number | null };
const MAX_RUNS = 6;
const MAX_BUFFER = 400_000;

export type LiveActivity = { terminal: boolean; browser: boolean };
export type LiveTab = "terminal" | "browser" | "site" | "files";
type Tab = LiveTab;

/**
 * What the agent is doing right now on the host, as the host sees it: its
 * terminal (tests, builds, servers), its browser (the running app, live) and,
 * when the host shares it, the running site itself to use.
 */
export function GuestLivePanel({
  client,
  chatId,
  open,
  site,
  siteTunnel,
  siteFocus,
  canDownload = false,
  filesSource,
  tabRequest,
  onTabChange,
  onActivity,
  onClose,
  className,
}: {
  client: NavinClient;
  chatId: string;
  open: boolean;
  /** The host shares its running site with this link. */
  site: boolean;
  siteTunnel: GuestSiteTunnel;
  /** Bumped when the host starts sharing the site: the Site tab comes forward. */
  siteFocus: number;
  /** The guest may download: the project's files tab. */
  canDownload?: boolean;
  filesSource?: GuestFilesSource;
  /** The header's Terminal / Browser buttons: bring that tab forward. */
  tabRequest?: { tab: "terminal" | "browser"; n: number };
  /** The tab on show, so the header can mark its button as active. */
  onTabChange?: (tab: Tab) => void;
  onActivity: (activity: LiveActivity) => void;
  onClose: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>(site ? "site" : "terminal");
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (siteFocus) setTab("site");
  }, [siteFocus]);
  useEffect(() => {
    if (tabRequest?.n) setTab(tabRequest.tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one switch per click
  }, [tabRequest?.n]);
  const tabChange = useRef(onTabChange);
  tabChange.current = onTabChange;
  useEffect(() => {
    tabChange.current?.(tab);
  }, [tab]);
  useEffect(() => {
    if (!site) setTab((current) => (current === "site" ? "terminal" : current));
  }, [site]);
  useEffect(() => {
    if (!canDownload) setTab((current) => (current === "files" ? "terminal" : current));
  }, [canDownload]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const buffers = useRef(new Map<string, string>());
  const listeners = useRef(new Map<string, Set<(chunk: string) => void>>());
  const activity = useRef(onActivity);
  activity.current = onActivity;

  const { session } = useAgentLiveSessions(client, chatId, () => {
    setTab("browser");
    activity.current({ terminal: false, browser: true });
  });

  const push = useCallback((id: string, chunk: string) => {
    const next = ((buffers.current.get(id) ?? "") + chunk).slice(-MAX_BUFFER);
    buffers.current.set(id, next);
    for (const handler of listeners.current.get(id) ?? []) handler(chunk);
  }, []);

  useEffect(
    () =>
      client.onAgentExec((update) => {
        if (update.chatId !== chatId) return;
        if (update.phase === "start") {
          buffers.current.set(update.id, "");
          if (update.command) push(update.id, `\x1b[1m$ ${update.command}\x1b[0m\r\n`);
          setRuns((prev) => [...prev.filter((run) => run.id !== update.id), {
            id: update.id, command: update.command ?? "", exited: false, exitCode: null,
          }].slice(-MAX_RUNS));
          setActive(update.id);
          setTab("terminal");
          activity.current({ terminal: true, browser: false });
          return;
        }
        if (update.phase === "output") {
          if (update.data) push(update.id, update.data);
          return;
        }
        const code = update.exitCode ?? null;
        push(update.id, `\r\n\x1b[2m[exit ${code ?? "?"}]\x1b[0m\r\n`);
        setRuns((prev) => prev.map((run) => (run.id === update.id ? { ...run, exited: true, exitCode: code } : run)));
      }),
    [chatId, client, push],
  );

  const backlog = useCallback((id: string) => buffers.current.get(id) ?? "", []);
  const subscribe = useCallback((id: string, handler: (chunk: string) => void) => {
    const set = listeners.current.get(id) ?? new Set();
    set.add(handler);
    listeners.current.set(id, set);
    return () => set.delete(handler);
  }, []);

  if (!open) return null;
  const tabs = [
    ...(site ? [{ id: "site" as const, icon: AppWindow, label: t("chat.liveShare.guest.site.tab", { defaultValue: "Site" }) }] : []),
    { id: "terminal" as const, icon: SquareTerminal, label: t("chat.liveShare.guest.livePanel.terminal", { defaultValue: "Terminal" }) },
    { id: "browser" as const, icon: Globe2, label: t("chat.liveShare.guest.livePanel.browser", { defaultValue: "Browser" }) },
    ...(canDownload && filesSource
      ? [{ id: "files" as const, icon: FolderOpen, label: t("chat.liveShare.guest.files.tab", { defaultValue: "Files" }) }]
      : []),
  ];

  return (
    <aside
      className={cn(
        "flex min-h-0 flex-col border-s border-border/70 bg-background",
        expanded ? "fixed inset-0 z-40 w-full min-w-0 border-s-0" : className,
      )}
    >
      <div className="flex items-center gap-1 border-b border-border/70 px-2 py-1.5">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={cn(
              "flex h-9 items-center gap-1.5 rounded-md px-2.5 text-[13px]",
              tab === item.id ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground",
            )}
          >
            <item.icon className="h-4 w-4" aria-hidden />
            {item.label}
            {item.id === "browser" && session?.live ? <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> : null}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-pressed={expanded}
          aria-label={expanded
            ? t("chat.liveShare.guest.site.shrink", { defaultValue: "Back beside the chat" })
            : t("chat.liveShare.guest.site.expand", { defaultValue: "Full screen" })}
          title={expanded
            ? t("chat.liveShare.guest.site.shrink", { defaultValue: "Back beside the chat" })
            : t("chat.liveShare.guest.site.expand", { defaultValue: "Full screen" })}
          className="ms-auto flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
        >
          {expanded ? <Minimize2 className="h-4 w-4" aria-hidden /> : <Maximize2 className="h-4 w-4" aria-hidden />}
        </button>
        <button
          type="button"
          onClick={() => {
            setExpanded(false);
            onClose();
          }}
          aria-label={t("common.close", { defaultValue: "Close" })}
          className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      {/* Kept mounted while another tab shows: the site keeps its page and state. */}
      {site ? <SiteView tunnel={siteTunnel} hidden={tab !== "site"} /> : null}
      {canDownload && filesSource ? <GuestFiles source={filesSource} hidden={tab !== "files"} /> : null}
      {tab === "site" || tab === "files" ? null : tab === "terminal" ? (
        runs.length === 0 ? (
          <Empty text={t("chat.liveShare.guest.livePanel.noCommand", { defaultValue: "Commands the agent runs (tests, builds, servers) appear here live." })} />
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex gap-1 overflow-x-auto border-b border-border/60 px-2 py-1">
              {runs.map((run) => (
                <button
                  key={run.id}
                  type="button"
                  onClick={() => setActive(run.id)}
                  title={run.command}
                  className={cn(
                    "flex max-w-[12rem] shrink-0 items-center gap-1.5 truncate rounded px-2 py-1 font-mono text-[11.5px]",
                    active === run.id ? "bg-muted" : "text-muted-foreground",
                  )}
                >
                  <span
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      !run.exited ? "animate-pulse bg-sky-500" : run.exitCode === 0 ? "bg-emerald-500" : "bg-amber-500",
                    )}
                  />
                  <span className="truncate">{run.command || run.id}</span>
                </button>
              ))}
            </div>
            <div className="relative min-h-0 flex-1">
              <Suspense fallback={<Empty text="" spinner />}>
                {active ? (
                  <AgentExecTerminal
                    key={active}
                    termId={active}
                    backlog={backlog}
                    subscribe={subscribe}
                    isDark={document.documentElement.classList.contains("dark")}
                    active
                  />
                ) : null}
              </Suspense>
            </div>
          </div>
        )
      ) : session?.frame ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="truncate border-b border-border/60 px-3 py-1.5 font-mono text-[11.5px] text-muted-foreground" title={session.url}>
            {session.url || " "}
          </div>
          <div className="flex min-h-0 flex-1 items-start justify-center overflow-auto bg-muted/30 p-2">
            <img
              src={`data:image/jpeg;base64,${session.frame}`}
              alt={t("chat.liveShare.guest.livePanel.browserAlt", { defaultValue: "The agent's browser, live" })}
              className="h-auto max-w-full rounded-md border border-border/60 shadow-sm"
              draggable={false}
            />
          </div>
          {session.actions.length ? (
            <div className="truncate border-t border-border/60 px-3 py-1.5 text-[12px] text-muted-foreground">
              {session.actions.at(-1)}
            </div>
          ) : null}
        </div>
      ) : (
        <Empty text={t("chat.liveShare.guest.livePanel.noBrowser", { defaultValue: "When the agent opens or tests the app in its browser, you see it here live." })} />
      )}
    </aside>
  );
}

/** The host's running site, served through the share by the guest's worker. */
export function SiteView({ tunnel, hidden }: { tunnel: GuestSiteTunnel; hidden: boolean }) {
  const { t } = useTranslation();
  const [state, setState] = useState<"starting" | "ready" | "unsupported">("starting");
  const [nonce, setNonce] = useState(0);
  const [path, setPath] = useState("/");
  const [loading, setLoading] = useState(true);
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    let alive = true;
    void tunnel.start().then((ok) => {
      if (alive) setState(ok ? "ready" : "unsupported");
    });
    return () => {
      alive = false;
    };
  }, [tunnel]);

  const reload = () => {
    setLoading(true);
    try {
      frameRef.current?.contentWindow?.location.reload();
    } catch {
      setNonce((n) => n + 1);
    }
  };

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", hidden && "hidden")}>
      <div className="flex items-center gap-1 border-b border-border/60 px-2 py-1">
        <button
          type="button"
          onClick={reload}
          disabled={state !== "ready"}
          aria-label={t("chat.liveShare.guest.site.reload", { defaultValue: "Reload the site" })}
          title={t("chat.liveShare.guest.site.reload", { defaultValue: "Reload the site" })}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted disabled:opacity-50"
        >
          {loading && state === "ready" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RotateCw className="h-4 w-4" aria-hidden />}
        </button>
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-muted-foreground" title={path}>
          {path}
        </span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          {t("chat.liveShare.guest.site.hint", { defaultValue: "Changes show after a reload" })}
        </span>
      </div>
      {state === "ready" ? (
        <iframe
          key={nonce}
          ref={frameRef}
          src="/"
          title={t("chat.liveShare.guest.site.title", { defaultValue: "The host's running site" })}
          className="min-h-0 w-full flex-1 border-0 bg-white"
          onLoad={() => {
            setLoading(false);
            try {
              const at = frameRef.current?.contentWindow?.location;
              if (at) setPath(at.pathname + at.search);
            } catch {
              // Left for another origin (an external link): the path stays as it was.
            }
          }}
        />
      ) : (
        <Empty
          spinner={state === "starting"}
          text={state === "starting"
            ? ""
            : t("chat.liveShare.guest.site.unsupported", {
                defaultValue: "This browser cannot open the shared site here (private browsing blocks it). Try a regular window.",
              })}
        />
      )}
    </div>
  );
}

function Empty({ text, spinner = false }: { text: string; spinner?: boolean }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center text-[13px] text-muted-foreground">
      {spinner ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
      {text}
    </div>
  );
}
