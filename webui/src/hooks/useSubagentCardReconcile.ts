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
