import { useCallback, useEffect, useRef, useState } from "react";

import { fetchSessionAutomations } from "@/lib/api";
import type { SessionAutomationJob } from "@/lib/types";

const AUTOMATIONS_REFRESH_MS = 3000;

/**
 * Load loops for a chat session.
 *
 * Always fetches once so the header can show an active badge. While `active`
 * (popover / editor open), polls every few seconds.
 */
export function useSessionAutomationJobs(active: boolean, token: string, sessionKey: string) {
  const [jobs, setJobs] = useState<SessionAutomationJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const loadedOnceRef = useRef(false);

  const refresh = useCallback(
    async (showLoading = false) => {
      if (showLoading) {
        setLoading(true);
        setLoadFailed(false);
      }
      try {
        const next = await fetchSessionAutomations(token, sessionKey);
        setJobs(next.jobs);
        setLoadFailed(false);
        loadedOnceRef.current = true;
      } catch {
        if (!loadedOnceRef.current) setLoadFailed(true);
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [sessionKey, token],
  );

  useEffect(() => {
    loadedOnceRef.current = false;
    setJobs([]);
    setLoadFailed(false);
    void refresh(true);
  }, [refresh]);

  useEffect(() => {
    if (!active) return;
    const refreshId = window.setInterval(() => void refresh(false), AUTOMATIONS_REFRESH_MS);
    const refreshOnFocus = () => {
      if (document.visibilityState !== "hidden") void refresh(false);
    };
    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshOnFocus);
    return () => {
      window.clearInterval(refreshId);
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshOnFocus);
    };
  }, [active, refresh]);

  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const tickId = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tickId);
  }, [active]);

  return { jobs, loading, loadFailed, now, refresh };
}
