// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";

import { extractJobId, isJobTerminal, jobFromStatus, latestProof } from "@/components/dev/proveChange";
import { flushDirtyFiles } from "@/lib/dirty-file-flusher";
import {
  enqueueEvolveCampaign,
  fetchEvolveOverview,
  fetchEvolveStatus,
  startEvolveDaemon,
  type DiagnosisReport,
  type ProofReport,
} from "@/lib/evolve-api";
import { setVisibleInterval } from "@/lib/visible-interval";
import { useClient } from "@/providers/ClientProvider";

export type ProveChangeState =
  | { phase: "idle" }
  | { phase: "starting" }
  | {
      phase: "started";
      job: number | null;
      /** When the run was queued (ms), for the elapsed time. */
      startedAt?: number;
      /** The daemon's latest word on the job: its state and what it is doing. */
      progress?: { state: string; detail?: string | null };
    }
  /** The run ended: its report when one was written, else what the engine said. */
  | {
      phase: "done";
      job: number | null;
      proof: ProofReport | null;
      diagnosis?: DiagnosisReport | null;
      detail?: string | null;
      startedAt?: number;
      endedAt?: number;
    }
  | { phase: "error"; message: string };

/** How often a running proof is checked while the window is visible. */
const PROOF_POLL_MS = 3_000;

export type ProveChange = {
  state: ProveChangeState;
  /** True when we know which project root Evolve should prove. */
  available: boolean;
  start: () => void;
  reset: () => void;
};

export type ProveChangeOptions = {
  /** Called after the campaign is accepted, so the UI can open Evolve. */
  onStarted?: (job: number | null) => void;
};

/**
 * "Prove this change": enqueue an Evolve robustness proof of the working
 * tree as it stands, pending (uncommitted) agent edits included. The run
 * itself lives in the Evolve tab; this only launches and reports the job.
 */
export function useProveChange(
  projectPath: string | null,
  options?: ProveChangeOptions,
): ProveChange {
  const { token } = useClient();
  const [state, setState] = useState<ProveChangeState>({ phase: "idle" });
  const busyRef = useRef(false);
  const onStartedRef = useRef(options?.onStarted);
  onStartedRef.current = options?.onStarted;

  const start = useCallback(() => {
    if (!token || !projectPath || busyRef.current) return;
    busyRef.current = true;
    setState({ phase: "starting" });
    void (async () => {
      try {
        // Editor buffers are not on disk until Save. A dirty proof without
        // this flush benches the last write and silently drops the buffer.
        await flushDirtyFiles();
        // Idempotent: brings the daemon up when needed, no-op when running.
        await startEvolveDaemon(token, projectPath);
        const reply = await enqueueEvolveCampaign(token, projectPath, "proof.run", {
          dirty: true,
        });
        const job = extractJobId(reply);
        startedAt.current = new Date().toISOString();
        setState({ phase: "started", job, startedAt: Date.now() });
        onStartedRef.current?.(job);
      } catch (err) {
        setState({
          phase: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      } finally {
        busyRef.current = false;
      }
    })();
  }, [projectPath, token]);

  // Follow the run to its end, so the button can say how it went instead of
  // "running" forever: the job leaves the daemon's list or reaches a final state.
  const startedAt = useRef<string>("");
  useEffect(() => {
    if (state.phase !== "started" || !token || !projectPath) return;
    const job = (state as Extract<ProveChangeState, { phase: "started" }>).job;
    if (!startedAt.current) startedAt.current = new Date().toISOString();
    let seen = false;
    let settled = false;
    const check = async () => {
      if (settled) return;
      try {
        const status = await fetchEvolveStatus(token, projectPath);
        const current = job === null ? null : jobFromStatus(status, job);
        if (current) seen = true;
        const finished = current ? isJobTerminal(current.state) : seen || job === null;
        if (!finished) {
          if (current) {
            setState((prev) =>
              prev.phase === "started"
              && prev.progress?.state === current.state
              && prev.progress?.detail === (current.detail ?? null)
                ? prev
                : { ...(prev as Extract<ProveChangeState, { phase: "started" }>), progress: { state: current.state, detail: current.detail ?? null } });
          }
          return;
        }
        settled = true;
        const overview = await fetchEvolveOverview(token, projectPath);
        const proof = latestProof(overview);
        // Only a report written by this run: older proofs stay in the Evolve tab.
        const since = Date.parse(startedAt.current) - 5_000;
        const fresh = proof && Date.parse(proof.collected_at) >= since ? proof : null;
        const diagnosis = [...(overview.diagnoses ?? [])]
          .filter((report) => Date.parse(report.collected_at) >= since)
          .sort((a, b) => (b.collected_at || "").localeCompare(a.collected_at || ""))[0] ?? null;
        setState((prev) => ({
          phase: "done",
          job,
          proof: fresh,
          diagnosis,
          detail: current?.detail ?? null,
          startedAt: prev.phase === "started" ? prev.startedAt : undefined,
          endedAt: Date.now(),
        }));
      } catch {
        // A missed poll is retried on the next tick.
      }
    };
    void check();
    const stop = setVisibleInterval(() => void check(), PROOF_POLL_MS);
    return () => {
      settled = true;
      stop();
    };
  }, [projectPath, state.phase, state.phase === "started" ? state.job : null, token]);

  const reset = useCallback(() => {
    startedAt.current = "";
    setState({ phase: "idle" });
  }, []);

  return { state, available: Boolean(token && projectPath), start, reset };
}
