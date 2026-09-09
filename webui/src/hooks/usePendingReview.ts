// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchReviewChanges, postReviewAction } from "@/lib/api";
import type { ReviewChangeEntry } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

/** Same cadence as the Dev Workbench: fast enough to feel live, cheap enough. */
const POLL_MS = 5_000;

export type PendingReview = {
  changes: ReviewChangeEntry[];
  busy: boolean;
  error: string | null;
  refresh: () => void;
  /** Accept or reject one file, or every pending file when path is omitted. */
  apply: (action: "accept" | "reject", path?: string) => void;
};

/**
 * Agent edits awaiting review for one session, for surfaces that only need the
 * list and the decisions (the chat). The Dev Workbench keeps its own copy: it
 * also needs baselines, hunks and a tree refresh after a rejection.
 */
export function usePendingReview(sessionKey: string | null): PendingReview {
  const { token } = useClient();
  const [changes, setChanges] = useState<ReviewChangeEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Polling must not fight an in-flight decision: a stale list would flash the
  // files that were just accepted back into the panel.
  const busyRef = useRef(false);

  const refresh = useCallback(() => {
    if (!token || !sessionKey) {
      setChanges([]);
      return;
    }
    if (busyRef.current) return;
    void fetchReviewChanges(token, sessionKey)
      .then((payload) => setChanges(payload.changes))
      .catch(() => undefined);
  }, [sessionKey, token]);

  useEffect(() => {
    setChanges([]);
    setError(null);
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const apply = useCallback(
    (action: "accept" | "reject", path?: string) => {
      if (!token || !sessionKey) return;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      // Optimistic: the decision is the user's, so the row leaves at once
      // instead of lingering until the next poll.
      setChanges((prev) => (path ? prev.filter((c) => c.path !== path) : []));
      void postReviewAction(token, sessionKey, action, path ?? null)
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          busyRef.current = false;
          setBusy(false);
          refresh();
        });
    },
    [refresh, sessionKey, token],
  );

  return { changes, busy, error, refresh, apply };
}
