// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Loader2, TerminalSquare, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { MontageSetupStreamEvent } from "@/lib/api";
import type { AgentExecUpdate } from "@/lib/navin-client";
import { useThemeValue } from "@/hooks/useTheme";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const AgentExecTerminal = lazy(() => import("@/components/dev/AgentExecTerminal"));

const BUFFER_CHARS = 200_000;

type Props = {
  open: boolean;
  busy: boolean;
  title?: string;
  /** NDJSON events replayed when the live WS feed was missed. */
  seedEvents?: MontageSetupStreamEvent[] | null;
  onClose: () => void;
};

/**
 * Read-only install console for Montage toolchain jobs.
 * Live output arrives as ``agent_exec`` frames while ``runMontageSetup`` runs.
 * ``seedEvents`` covers the case where the HTTP NDJSON body is the only feed.
 */
export function MontageInstallConsole({
  open,
  busy,
  title,
  seedEvents,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const { client } = useClient();
  const theme = useThemeValue();
  const isDark = theme === "dark";
  const [collapsed, setCollapsed] = useState(false);
  const [termId, setTermId] = useState<string | null>(null);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [headerCommand, setHeaderCommand] = useState<string>("");
  const buffersRef = useRef(new Map<string, string>());
  const listenersRef = useRef(new Map<string, Set<(chunk: string) => void>>());

  const pushChunk = useCallback((id: string, chunk: string) => {
    const prev = buffersRef.current.get(id) ?? "";
    const next = prev + chunk;
    buffersRef.current.set(
      id,
      next.length > BUFFER_CHARS ? next.slice(-BUFFER_CHARS) : next,
    );
    for (const handler of listenersRef.current.get(id) ?? []) {
      handler(chunk);
    }
  }, []);

  const backlog = useCallback((id: string) => buffersRef.current.get(id) ?? "", []);

  const subscribe = useCallback((id: string, handler: (chunk: string) => void) => {
    let set = listenersRef.current.get(id);
    if (!set) {
      set = new Set();
      listenersRef.current.set(id, set);
    }
    set.add(handler);
    return () => {
      set?.delete(handler);
    };
  }, []);

  useEffect(() => {
    // Stay subscribed even while collapsed/hidden so the start frame is not
    // missed between setState(open) and the HTTP round-trip.
    return client.onAgentExec((update: AgentExecUpdate) => {
      const id = `agent-${update.id}`;
      const known = buffersRef.current.has(id);
      if (update.phase === "start") {
        // Gateway tags Montage installs as ``navin montage setup …``.
        if (!update.command?.includes("montage setup")) return;
        buffersRef.current.set(id, "");
        setTermId(id);
        setExitCode(null);
        setCollapsed(false);
        if (update.command) {
          setHeaderCommand(update.command);
          const where = update.cwd ? `\x1b[2m${update.cwd}\x1b[0m\r\n` : "";
          pushChunk(id, `${where}\x1b[1m$ ${update.command}\x1b[0m\r\n`);
        }
        return;
      }
      if (!known) return;
      if (update.phase === "output") {
        if (update.data) pushChunk(id, update.data);
        return;
      }
      const code = update.exitCode ?? null;
      setExitCode(code);
      pushChunk(id, `\r\n\x1b[2m[exit ${code ?? "?"}]\x1b[0m\r\n`);
    });
  }, [client, pushChunk]);

  useEffect(() => {
    if (!seedEvents?.length) return;
    // If the live WS feed already populated a real buffer, keep it.
    const existingId = termId;
    if (
      existingId &&
      !existingId.startsWith("seed-pending-") &&
      (buffersRef.current.get(existingId) || "").length > 40
    ) {
      return;
    }
    const start = seedEvents.find((e) => e.type === "start");
    const id = `seed-${start?.id || "montage"}`;
    // Replaying the same pending start must not wipe chunks already appended
    // (ERROR / done) after a failed fetch.
    if (!buffersRef.current.has(id)) {
      buffersRef.current.set(id, "");
    }
    setTermId(id);
    setCollapsed(false);
    if (start?.command && !(buffersRef.current.get(id) || "").includes(start.command)) {
      setHeaderCommand(start.command);
      const where = start.cwd ? `\x1b[2m${start.cwd}\x1b[0m\r\n` : "";
      pushChunk(id, `${where}\x1b[1m$ ${start.command}\x1b[0m\r\n`);
    }
    for (const ev of seedEvents) {
      if (ev.type === "log" && ev.data) {
        const prev = buffersRef.current.get(id) || "";
        if (!prev.includes(ev.data)) pushChunk(id, ev.data);
      }
      if (ev.type === "done") {
        const code = ev.exit_code ?? null;
        setExitCode(code);
        const marker = `[exit ${code ?? "?"}]`;
        if (!(buffersRef.current.get(id) || "").includes(marker)) {
          pushChunk(id, `\r\n\x1b[2m${marker}\x1b[0m\r\n`);
        }
      }
    }
  }, [pushChunk, seedEvents, termId]);

  if (!open) return null;

  const label =
    title ||
    t("montage.console.title", { defaultValue: "Install console" });

  return (
    <div
      className={cn(
        "shrink-0 border-t border-border/55 bg-[#111318] text-[#e6e8ee]",
        collapsed ? "h-10" : "h-[min(38vh,320px)]",
      )}
    >
      <div className="flex h-10 items-center gap-2 border-b border-white/10 px-3">
        <TerminalSquare className="h-3.5 w-3.5 shrink-0 opacity-80" aria-hidden />
        <div className="min-w-0 flex-1 truncate text-[12px] font-medium">
          {label}
          {headerCommand ? (
            <span className="ml-2 font-normal text-white/55">{headerCommand}</span>
          ) : null}
        </div>
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-300" aria-hidden />
        ) : exitCode != null ? (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
              exitCode === 0
                ? "bg-emerald-500/20 text-emerald-300"
                : "bg-rose-500/20 text-rose-300",
            )}
          >
            {exitCode === 0
              ? t("montage.console.ok", { defaultValue: "Done" })
              : t("montage.console.failed", { defaultValue: "Failed" })}
          </span>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 shrink-0 p-0 text-white/70 hover:bg-white/10 hover:text-white"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={
            collapsed
              ? t("montage.console.expand", { defaultValue: "Expand console" })
              : t("montage.console.collapse", { defaultValue: "Collapse console" })
          }
        >
          {collapsed ? (
            <ChevronUp className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
          )}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 shrink-0 p-0 text-white/70 hover:bg-white/10 hover:text-white"
          onClick={onClose}
          disabled={busy}
          aria-label={t("montage.console.close", { defaultValue: "Close console" })}
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </Button>
      </div>
      {!collapsed ? (
        <div className="h-[calc(100%-2.5rem)] min-h-0">
          {termId ? (
            <Suspense
              fallback={
                <div className="flex h-full items-center justify-center text-[12px] text-white/50">
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" aria-hidden />
                  {t("montage.console.loading", { defaultValue: "Opening console…" })}
                </div>
              }
            >
              <AgentExecTerminal
                termId={termId}
                backlog={backlog}
                subscribe={subscribe}
                isDark={isDark}
                active
              />
            </Suspense>
          ) : (
            <div className="flex h-full items-center px-3 text-[12px] text-white/55">
              {t("montage.console.waiting", {
                defaultValue: "Starting install.",
              })}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
