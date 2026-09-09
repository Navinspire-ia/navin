// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Activity, ChevronDown, ChevronRight, RefreshCw, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { type BackgroundProcess, fetchProcesses, killProcess } from "@/lib/api";
import { cn } from "@/lib/utils";

import { formatElapsed, processStatus, shortCommand, tailLines } from "./processes";

const POLL_MS = 4000;

/**
 * Activity monitor for the agent's background sessions (dev servers,
 * watchers, workers started with background=true): live list, output tail,
 * one-click stop.
 */
export function DevProcessesPanel({ token }: { token: string }) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [processes, setProcesses] = useState<BackgroundProcess[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [killing, setKilling] = useState<string | null>(null);
  const aliveRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const payload = await fetchProcesses(token);
      if (!aliveRef.current) return;
      setProcesses(payload.processes);
      setError(null);
    } catch (err) {
      if (!aliveRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (aliveRef.current) setLoaded(true);
    }
  }, [token]);

  useEffect(() => {
    aliveRef.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      aliveRef.current = false;
      window.clearInterval(timer);
    };
  }, [refresh]);

  const stop = useCallback(
    async (id: string) => {
      setKilling(id);
      try {
        await killProcess(token, id);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (aliveRef.current) setKilling(null);
      }
    },
    [token, refresh],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 px-2 pb-1.5 pt-1">
        <span className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
          <Activity className="h-3.5 w-3.5" aria-hidden />
          {tx("dev.processes.title", "Background processes")}
          {processes.length > 0 ? (
            <span className="rounded-full bg-muted px-1.5 text-[10px] font-semibold">
              {processes.length}
            </span>
          ) : null}
        </span>
        <button
          type="button"
          onClick={() => void refresh()}
          className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          title={tx("dev.processes.refresh", "Refresh")}
          aria-label={tx("dev.processes.refresh", "Refresh")}
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      {error ? (
        <p className="px-2 pb-1 text-[10.5px] text-destructive" title={error}>
          {error}
        </p>
      ) : null}
      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {loaded && processes.length === 0 ? (
          <p className="px-2 py-3 text-center text-[11px] text-muted-foreground">
            {tx(
              "dev.processes.empty",
              "No background process. The agent's dev servers and watchers will appear here.",
            )}
          </p>
        ) : null}
        <ul className="flex flex-col gap-1">
          {processes.map((proc) => {
            const status = processStatus(proc);
            const isOpen = expanded === proc.id;
            return (
              <li
                key={proc.id}
                className="rounded-md border border-border/50 bg-background/60"
              >
                <div className="flex items-center gap-1.5 px-1.5 py-1">
                  <button
                    type="button"
                    onClick={() => setExpanded(isOpen ? null : proc.id)}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                    title={proc.command}
                  >
                    {isOpen ? (
                      <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                    ) : (
                      <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                    )}
                    <span
                      className={cn(
                        "h-1.5 w-1.5 shrink-0 rounded-full",
                        status === "running"
                          ? "bg-emerald-500"
                          : status === "exited"
                            ? "bg-muted-foreground/60"
                            : "bg-destructive",
                      )}
                      aria-hidden
                    />
                    <span className="min-w-0 truncate font-mono text-[11px] text-foreground">
                      {shortCommand(proc.command)}
                    </span>
                  </button>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                    {formatElapsed(proc.elapsed_s)}
                  </span>
                  <button
                    type="button"
                    onClick={() => void stop(proc.id)}
                    disabled={killing === proc.id}
                    className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                    title={tx("dev.processes.stop", "Stop the process")}
                    aria-label={tx("dev.processes.stop", "Stop the process")}
                  >
                    <Square className="h-3 w-3" aria-hidden />
                  </button>
                </div>
                {isOpen ? (
                  <div className="border-t border-border/40 px-2 py-1.5">
                    <p
                      className="truncate pb-1 font-mono text-[10px] text-muted-foreground"
                      title={proc.cwd}
                    >
                      {proc.cwd}
                    </p>
                    {proc.tail ? (
                      <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-all rounded bg-muted/40 p-1.5 font-mono text-[10px] leading-4 text-muted-foreground">
                        {tailLines(proc.tail).join("\n")}
                      </pre>
                    ) : (
                      <p className="text-[10.5px] italic text-muted-foreground">
                        {tx("dev.processes.noOutput", "No output yet.")}
                      </p>
                    )}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
