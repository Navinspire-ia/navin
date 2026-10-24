// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";

import { fetchProjectRules } from "@/lib/api";
import { fetchBoardShared } from "@/lib/board-fetch";
import {
  PROJECT_RULES_CHANGED_EVENT,
  countBoardTasks,
  type RailTaskCounts,
} from "@/lib/rail-counts";
import { useLatestTokenRef } from "@/hooks/useLatestToken";
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
  // The rotating token is read at call time: a rotation is not a reason to reload.
  const tokenRef = useLatestTokenRef();
  const hasToken = Boolean(token);
  const [counts, setCounts] = useState<RailCounts>(EMPTY);

  const loadRules = useCallback(async () => {
    if (!tokenRef.current) return;
    try {
      const payload = await fetchProjectRules(tokenRef.current, sessionKey);
      setCounts((prev) => ({ ...prev, rules: payload.total ?? payload.rules.length }));
    } catch {
      // A missing project or an offline gateway simply leaves the pill off.
      setCounts((prev) => ({ ...prev, rules: null }));
    }
  }, [tokenRef, sessionKey]);

  const loadTasks = useCallback(async () => {
    if (!tokenRef.current) return;
    try {
      const payload = await fetchBoardShared(tokenRef.current, sessionKey);
      setCounts((prev) => ({ ...prev, tasks: countBoardTasks(payload.tasks) }));
    } catch {
      setCounts((prev) => ({ ...prev, tasks: null }));
    }
  }, [tokenRef, sessionKey]);

  useEffect(() => {
    if (!enabled || !hasToken) return undefined;
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
  }, [enabled, hasToken, client, projectPath, loadRules, loadTasks]);

  return counts;
}
