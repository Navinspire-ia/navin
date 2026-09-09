// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Loader2, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { flushDirtyFiles } from "@/lib/dirty-file-flusher";
import {
  cancelEvolveJob,
  enqueueEvolveCampaign,
  fetchEvolveOverview,
  fetchEvolveStatus,
  startEvolveDaemon,
} from "@/lib/evolve-api";
import { cn } from "@/lib/utils";

import {
  extractJobId,
  isJobTerminal,
  jobFromStatus,
  latestProof,
  proofSummary,
  verdictTone,
} from "./proveChange";

type Phase = "idle" | "starting" | "running" | "done" | "error";

const POLL_MS = 3000;

/**
 * "Prove this change" in the source-control panel: runs the deterministic
 * Evolve proof bench (fault injection + robustness score) on the *working
 * tree* (dirty), so the pending change is judged before it is committed.
 */
export function DevProveChange({
  token,
  projectPath,
}: {
  token: string;
  projectPath: string | null;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [phase, setPhase] = useState<Phase>("idle");
  const [summary, setSummary] = useState("");
  const [tone, setTone] = useState<"good" | "warn" | "bad" | "muted">("muted");
  const [error, setError] = useState<string | null>(null);
  const jobRef = useRef<number | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const finish = useCallback(
    async (path: string) => {
      try {
        const overview = await fetchEvolveOverview(token, path);
        const proof = latestProof(overview);
        setSummary(proofSummary(proof));
        setTone(verdictTone(proof?.verdict ?? null));
        setPhase("done");
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setPhase("error");
      }
    },
    [token],
  );

  const poll = useCallback(
    async (path: string, jobId: number) => {
      while (!cancelledRef.current) {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        if (cancelledRef.current) return;
        try {
          const status = await fetchEvolveStatus(token, path);
          const job = jobFromStatus(status, jobId);
          // Job gone from the queue = finished and flushed.
          if (job === null || isJobTerminal(job.state)) {
            if (job?.state === "failed" || job?.state === "error") {
              setError(job.detail || tx("dev.prove.failed", "Proof run failed."));
              setPhase("error");
              return;
            }
            await finish(path);
            return;
          }
        } catch {
          // Transient gateway error: keep polling.
        }
      }
    },
    [token, finish, tx],
  );

  const run = useCallback(async () => {
    if (!projectPath) return;
    setPhase("starting");
    setError(null);
    setSummary("");
    try {
      await flushDirtyFiles();
      await startEvolveDaemon(token, projectPath);
      const result = await enqueueEvolveCampaign(token, projectPath, "proof.run", {
        dirty: true,
      });
      const jobId = extractJobId(result);
      if (jobId === null) {
        // Enqueued without an id: fall back to showing the latest report
        // after a generous delay would be guesswork; report it plainly.
        setError(tx("dev.prove.noJob", "The daemon did not return a job id."));
        setPhase("error");
        return;
      }
      jobRef.current = jobId;
      setPhase("running");
      void poll(projectPath, jobId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }, [token, projectPath, poll, tx]);

  const stop = useCallback(() => {
    const jobId = jobRef.current;
    if (projectPath && jobId !== null) {
      void cancelEvolveJob(token, projectPath, jobId).catch(() => {});
    }
    cancelledRef.current = true;
    setPhase("idle");
    setSummary("");
  }, [token, projectPath]);

  if (!projectPath) return null;
  const busy = phase === "starting" || phase === "running";

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <button
        type="button"
        onClick={() => (busy ? stop() : void run())}
        className={cn(
          "flex shrink-0 items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[10.5px] font-medium transition-colors",
          busy
            ? "text-amber-600 hover:bg-amber-500/10"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
        title={tx(
          "dev.prove.hint",
          "Runs the Evolve proof bench (fault injection, robustness score) on the current working tree.",
        )}
      >
        {busy ? (
          <>
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {phase === "starting"
              ? tx("dev.prove.starting", "Starting…")
              : tx("dev.prove.running", "Proving…")}
            <X className="h-3 w-3 opacity-70" aria-hidden />
          </>
        ) : (
          <>
            <ShieldCheck className="h-3 w-3" aria-hidden />
            {tx("dev.prove.action", "Prove")}
          </>
        )}
      </button>
      {phase === "done" && summary ? (
        <span
          className={cn(
            "min-w-0 truncate font-mono text-[10.5px]",
            tone === "good"
              ? "text-emerald-600"
              : tone === "warn"
                ? "text-amber-600"
                : tone === "bad"
                  ? "text-destructive"
                  : "text-muted-foreground",
          )}
          title={summary}
        >
          {summary}
        </span>
      ) : null}
      {phase === "error" && error ? (
        <span
          className="min-w-0 truncate text-[10.5px] text-destructive"
          title={error}
        >
          {error}
        </span>
      ) : null}
    </div>
  );
}
