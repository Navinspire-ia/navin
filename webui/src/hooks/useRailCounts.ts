import { useCallback, useEffect, useState } from "react";

import { fetchBoard, fetchProjectRules } from "@/lib/api";
import {
  PROJECT_RULES_CHANGED_EVENT,
  countBoardTasks,
  type RailTaskCounts,
} from "@/lib/rail-counts";
import { useClient } from "@/providers/ClientProvider";

export interface RailCounts {
  /** Rule files under .navin/rules; null until the first answer. */
  rules: number | null;
  tasks: RailTaskCounts | null;
}

const EMPTY: RailCounts = { rules: null, tasks: null };

/**
 * Rules and Tasks counters for the workbench rail. Fetched when the rail is
 * visible, then kept fresh from the gateway's ``board_updated`` broadcast and
 * the Rules panel's change event - no polling.
 */
export function useRailCounts({
  sessionKey,
  projectPath,
  enabled,
}: {
  sessionKey: string;
  projectPath: string | null | undefined;
  enabled: boolean;
}): RailCounts {
  const { token, client } = useClient();
  const [counts, setCounts] = useState<RailCounts>(EMPTY);

  const loadRules = useCallback(async () => {
    if (!token) return;
    try {
      const payload = await fetchProjectRules(token, sessionKey);
      setCounts((prev) => ({ ...prev, rules: payload.total ?? payload.rules.length }));
    } catch {
      // A missing project or an offline gateway simply leaves the pill off.
      setCounts((prev) => ({ ...prev, rules: null }));
    }
  }, [token, sessionKey]);

  const loadTasks = useCallback(async () => {
    if (!token) return;
    try {
      const payload = await fetchBoard(token, sessionKey);
      setCounts((prev) => ({ ...prev, tasks: countBoardTasks(payload.tasks) }));
    } catch {
      setCounts((prev) => ({ ...prev, tasks: null }));
    }
  }, [token, sessionKey]);

  useEffect(() => {
    if (!enabled || !token) return undefined;
    void loadRules();
    void loadTasks();
    const unsubscribe = client.onBoardUpdate((changedProject) => {
      if (changedProject && projectPath && changedProject !== projectPath) return;
      void loadTasks();
    });
    const onRulesChanged = () => void loadRules();
    window.addEventListener(PROJECT_RULES_CHANGED_EVENT, onRulesChanged);
    return () => {
      unsubscribe();
      window.removeEventListener(PROJECT_RULES_CHANGED_EVENT, onRulesChanged);
    };
  }, [enabled, token, client, projectPath, loadRules, loadTasks]);

  return counts;
}
