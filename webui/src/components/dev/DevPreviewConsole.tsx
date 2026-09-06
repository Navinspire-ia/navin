import { AlertTriangle, Bug, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { PreviewLogEntry } from "@/lib/api";
import { clearPreviewLogs, fetchPreviewLogs } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  isProblemLevel,
  mergePreviewEntries,
  problemCount,
  seedTextFromDigest,
} from "./previewConsole";

const POLL_MS = 2500;

/**
 * Live console of the instrumented preview (console output, uncaught errors,
 * failed network calls captured by the injected probe). "Fix with agent"
 * seeds the chat composer with a digest of the problems; the user completes
 * and sends it.
 */
export function DevPreviewConsole({
  token,
  port,
  onSeed,
  onProblemCount,
  onClose,
}: {
  token: string;
  /** Target port of the previewed app (not the proxy port). */
  port: number;
  onSeed?: (text: string) => void;
  onProblemCount?: (count: number) => void;
  onClose?: () => void;
}) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<PreviewLogEntry[]>([]);
  const [digest, setDigest] = useState("");
  const [problemsOnly, setProblemsOnly] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    setEntries([]);
    setDigest("");
  }, [port]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const last = 0; // digest needs the full window; the payload is small
        const payload = await fetchPreviewLogs(token, port, last);
        if (cancelled) return;
        setEntries((existing) => mergePreviewEntries(existing, payload.entries));
        setDigest(payload.digest || "");
      } catch {
        // Gateway briefly unreachable: keep the last snapshot.
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, POLL_MS);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [token, port]);

  const problems = problemCount(entries);
  useEffect(() => {
    onProblemCount?.(problems);
  }, [problems, onProblemCount]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node && stickToBottomRef.current) {
      node.scrollTop = node.scrollHeight;
    }
  }, [entries]);

  const handleScroll = useCallback(() => {
    const node = scrollRef.current;
    if (!node) return;
    stickToBottomRef.current =
      node.scrollHeight - node.scrollTop - node.clientHeight < 24;
  }, []);

  const handleClear = useCallback(() => {
    setEntries([]);
    setDigest("");
    void clearPreviewLogs(token, port).catch(() => {});
  }, [token, port]);

  const handleSeed = useCallback(() => {
    const text = seedTextFromDigest(digest, port);
    if (text) onSeed?.(text);
  }, [digest, port, onSeed]);

  const visible = problemsOnly
    ? entries.filter((entry) => isProblemLevel(entry.level))
    : entries;

  return (
    <div className="flex h-full min-h-0 flex-col border-t border-border/60 bg-background">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/50 px-2 py-1">
        <span className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("dev.previewConsole.title", { defaultValue: "Preview console" })}
          <span className="font-normal normal-case tabular-nums text-muted-foreground/80">
            {t("dev.previewConsole.port", { defaultValue: "port" })} {port}
            {" · "}
            {problems}{" "}
            {t("dev.previewConsole.problems", { defaultValue: "problem(s)" })}
          </span>
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setProblemsOnly((v) => !v)}
            className={cn(
              "rounded px-1.5 py-0.5 text-[11px] transition-colors",
              problemsOnly
                ? "bg-amber-500/15 text-amber-600"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            title={t("dev.previewConsole.problemsOnly", {
              defaultValue: "Show problems only",
            })}
          >
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
          </button>
          {onSeed ? (
            <button
              type="button"
              onClick={handleSeed}
              disabled={!digest}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-45"
              title={t("dev.previewConsole.fixHint", {
                defaultValue:
                  "Seeds the chat with the captured problems so the agent can fix them.",
              })}
            >
              <Bug className="h-3.5 w-3.5" aria-hidden />
              {t("dev.previewConsole.fix", { defaultValue: "Fix with agent" })}
            </button>
          ) : null}
          <button
            type="button"
            onClick={handleClear}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={t("dev.previewConsole.clear", { defaultValue: "Clear" })}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
          </button>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={t("dev.previewConsole.close", {
                defaultValue: "Close console",
              })}
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          ) : null}
        </div>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="min-h-0 flex-1 overflow-y-auto font-mono text-[11.5px] leading-snug"
      >
        {visible.length === 0 ? (
          <p className="px-3 py-3 font-sans text-[12px] text-muted-foreground">
            {t("dev.previewConsole.empty", {
              defaultValue:
                "No output captured yet. Interact with the preview to see its console here.",
            })}
          </p>
        ) : (
          visible.map((entry) => (
            <div
              key={entry.id}
              className={cn(
                "whitespace-pre-wrap break-words border-b border-border/30 px-2 py-1",
                entry.level === "error" || entry.level === "pageerror"
                  ? "bg-red-500/5 text-red-600 dark:text-red-400"
                  : entry.level === "network"
                    ? "bg-amber-500/5 text-amber-700 dark:text-amber-400"
                    : entry.level === "warn"
                      ? "text-amber-600 dark:text-amber-300"
                      : "text-foreground/85",
              )}
            >
              <span className="mr-1.5 select-none text-[10px] uppercase opacity-60">
                {entry.level}
              </span>
              {entry.text}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
