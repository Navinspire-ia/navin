// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useRef, useState } from "react";

import { extractJobId } from "@/components/dev/proveChange";
import { flushDirtyFiles } from "@/lib/dirty-file-flusher";
import { enqueueEvolveCampaign, startEvolveDaemon } from "@/lib/evolve-api";
import { useClient } from "@/providers/ClientProvider";

export type ProveChangeState =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "started"; job: number | null }
  | { phase: "error"; message: string };

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
        setState({ phase: "started", job });
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

  const reset = useCallback(() => setState({ phase: "idle" }), []);

  return { state, available: Boolean(token && projectPath), start, reset };
}
