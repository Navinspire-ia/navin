import { useEffect } from "react";
import type { NavinClient, SubagentRecord } from "@/lib/navin-client";
import type { SubagentCardStore } from "@/components/thread/ParallelSubagentsPanel";
import { reconcileCards } from "@/components/thread/SubagentsSidePanel";

/**
 * While a live card still says "running", check the durable journal now and
 * then. The final progress frame can be lost (socket reconnect, a tab in the
 * background), which used to leave finished agents running on screen forever.
 */
export function useSubagentCardReconcile(
  client: Pick<NavinClient, "querySubagents">,
  chatId: string | null | undefined,
  store: SubagentCardStore,
  intervalMs = 5000,
): void {
  useEffect(() => {
    if (!chatId) return;
    let live = true;
    let pending = false;
    const tick = async () => {
      if (pending || !store.getSnapshot().cards.some((card) => !card.done)) return;
      pending = true;
      try {
        const rows: SubagentRecord[] = [];
        for (let offset = 0; live; offset += 200) {
          const page = await client.querySubagents<SubagentRecord[]>(chatId, "list", { offset, limit: 200 });
          if (!Array.isArray(page)) break;
          rows.push(...page);
          if (page.length < 200) break;
        }
        if (live) reconcileCards(store, rows);
      } catch {
        // Best effort: the next tick or the next live frame will settle it.
      } finally {
        pending = false;
      }
    };
    const timer = window.setInterval(() => void tick(), intervalMs);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [client, chatId, store, intervalMs]);
}

/** Failed agents restored after a refresh, newest last. */
export const RESTORE_FAILED_MAX = 10;

/**
 * On chat open, bring back the agents that ended and can still be relaunched
 * or recovered: the live frames that showed them are gone after a refresh,
 * and a failure nobody can see is a failure nobody can fix.
 */
export function useSubagentFailedRestore(
  client: Pick<NavinClient, "querySubagents">,
  chatId: string | null | undefined,
  store: SubagentCardStore,
): void {
  useEffect(() => {
    if (!chatId) return;
    let live = true;
    void (async () => {
      try {
        const rows: SubagentRecord[] = [];
        for (let offset = 0; live; offset += 200) {
          const page = await client.querySubagents<SubagentRecord[]>(chatId, "list", { offset, limit: 200 });
          if (!Array.isArray(page)) break;
          rows.push(...page);
          if (page.length < 200) break;
        }
        if (!live) return;
        const known = new Set(store.getSnapshot().cards.map((card) => card.taskId));
        const actionable = rows.filter(
          (record) =>
            (record.done || record.phase === "interrupted") &&
            (record.recoverable || record.requires_relaunch) &&
            !store.isDismissed(record.task_id) &&
            !known.has(record.task_id),
        );
        for (const record of actionable.slice(-RESTORE_FAILED_MAX)) {
          store.upsert({
            chatId,
            taskId: record.task_id,
            label: record.label,
            phase: record.phase,
            statusLine: record.status_line || "",
            model: record.model,
            taskDescription: record.task_description,
            iteration: 0,
            done: true,
            error: record.error || undefined,
          });
        }
      } catch {
        // Best effort: live frames still show anything that changes.
      }
    })();
    return () => {
      live = false;
    };
  }, [client, chatId, store]);
}
