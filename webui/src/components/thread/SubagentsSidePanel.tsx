import { createContext, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowLeft, Loader2, Users, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { MarkdownText } from "@/components/MarkdownText";
import { cn } from "@/lib/utils";
import type { NavinClient, SubagentHistory, SubagentHistoryEvent, SubagentRecord } from "@/lib/navin-client";
import type { SubagentCardStore } from "./ParallelSubagentsPanel";

export function SubagentsButton({ store, open, onClick, hasHistory = false }: { store: SubagentCardStore; open: boolean; onClick: () => void; hasHistory?: boolean }) {
  const { t } = useTranslation();
  const { cards } = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const active = cards.filter((card) => !card.done).length;
  // Finished agents stay reachable: the button remains while the chat has any history.
  if (!active && !hasHistory && !cards.length) return null;
  // Same reading as the journal sections: icon, title, count in the margin.
  return <Button variant="ghost" size="sm" onClick={onClick} aria-expanded={open} aria-controls="subagents-side-panel" className="host-no-drag h-9 gap-2 rounded-md px-2 text-[14px] font-semibold text-foreground/90 hover:bg-foreground/[0.04]">
    {active
      ? <Loader2 className="h-[18px] w-[18px] animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden />
      : <Users className="h-[18px] w-[18px] text-muted-foreground" aria-hidden />}
    {t("thread.subagents.title", { defaultValue: "Subagents" })}
    {active ? <span className="text-[13.5px] font-normal tabular-nums text-muted-foreground">{active}</span> : null}
  </Button>;
}

/** What the journal needs to show the turn's sub-agents: their live cards and the panel with their details. */
export const SubagentJournalContext = createContext<{ store?: SubagentCardStore; openPanel?: () => void }>({});

export type SubagentState = "running" | "completed" | "failed" | "cancelled" | "interrupted";

/** One state per record, from the durable journal, never from a stale live frame. */
export function subagentState(record: Pick<SubagentRecord, "done" | "phase" | "error">): SubagentState {
  if (["interrupted", "interrupted_tool"].includes(record.phase)) return "interrupted";
  if (!record.done) return "running";
  const error = String(record.error || "").toLowerCase();
  if (error === "cancelled" || error === "canceled") return "cancelled";
  return record.phase === "error" || error ? "failed" : "completed";
}

const STATE_LABEL: Record<SubagentState, string> = { running: "Running", completed: "Completed", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" };
const STATE_CLASS: Record<SubagentState, string> = {
  running: "bg-primary/10 text-primary",
  completed: "bg-muted text-muted-foreground",
  failed: "bg-destructive/10 text-destructive",
  cancelled: "bg-muted text-muted-foreground",
  interrupted: "bg-muted text-muted-foreground",
};

export function StateBadge({ state }: { state: SubagentState }) {
  const { t } = useTranslation();
  return <span data-state={state} className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", STATE_CLASS[state])}>{t(`thread.subagents.state.${state}`, { defaultValue: STATE_LABEL[state] })}</span>;
}

function parseArgs(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try { return JSON.parse(value); } catch { return value; }
}

function short(value: unknown, max = 60): string {
  const text = typeof value === "string" ? value : JSON.stringify(value) ?? "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 3)}...` : flat;
}

/** "path: a.py, limit: 20" from a tool's arguments, whatever shape they came in. */
export function summarizeArgs(value: unknown): string {
  const args = parseArgs(value);
  if (args === undefined || args === null || args === "") return "";
  if (typeof args !== "object" || Array.isArray(args)) return short(args, 80);
  return Object.entries(args as Record<string, unknown>).slice(0, 2).map(([key, item]) => `${key}: ${short(item, 40)}`).join(", ");
}

function fullText(value: unknown): string {
  const parsed = parseArgs(value);
  return typeof parsed === "string" ? parsed : JSON.stringify(parsed ?? {}, null, 2);
}

function ToolLine({ name, args, label }: { name: string; args: unknown; label?: string }) {
  const summary = summarizeArgs(args);
  const full = fullText(args);
  return <details className="group rounded-md border border-border/60 bg-muted/20 px-2 py-1 text-xs">
    <summary className="flex cursor-pointer list-none items-center gap-2 text-muted-foreground"><span className="font-mono font-medium text-foreground">{name}</span>{label ? <span>{label}</span> : null}<span className="min-w-0 flex-1 truncate">{summary}</span></summary>
    {full && full !== "{}" ? <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-muted-foreground">{full}</pre> : null}
  </details>;
}

function toolCallsOf(payload: Record<string, unknown>): { name: string; args: unknown }[] {
  if (!Array.isArray(payload.tool_calls)) return [];
  return payload.tool_calls.map((call) => {
    const item = (call ?? {}) as Record<string, unknown>;
    const fn = (item.function ?? {}) as Record<string, unknown>;
    return { name: String(fn.name || item.name || "tool"), args: fn.arguments ?? item.arguments };
  });
}

function EventBody({ event }: { event: SubagentHistoryEvent }) {
  const { t } = useTranslation();
  const p = event.payload;
  if (event.kind === "tool_start" || event.kind === "tool_done") {
    const done = event.kind === "tool_done";
    return <ToolLine name={String(p.name || "tool")} args={done ? p.result : p.arguments} label={done ? t("thread.subagents.toolResult", { defaultValue: "result" }) : undefined} />;
  }
  const text = typeof p.content === "string" ? p.content : event.kind === "phase" ? String(p.status_line || p.phase || "") : "";
  const calls = toolCallsOf(p);
  return <>
    {text ? <MarkdownText className="text-[13px]">{text}</MarkdownText> : null}
    {calls.length ? <ul className="mt-2 space-y-1">{calls.map((call, index) => <li key={index}><ToolLine name={call.name} args={call.args} /></li>)}</ul> : null}
    {!text && !calls.length && event.kind !== "phase" ? <p className="text-xs text-muted-foreground">{summarizeArgs(p)}</p> : null}
  </>;
}

/** Close live cards the journal already knows are over: a lost final frame
 * (reconnect, background tab) otherwise left them "running" for good. */
export function reconcileCards(store: SubagentCardStore, records: SubagentRecord[]): void {
  const byId = new Map(records.map((record) => [record.task_id, record]));
  for (const card of store.getSnapshot().cards) {
    const record = byId.get(card.taskId);
    if (card.done || !record || subagentState(record) === "running") continue;
    store.upsert({ ...card, done: true, phase: record.phase, statusLine: record.status_line || card.statusLine, error: record.error || card.error });
  }
}

/** A separate reading surface: opening an agent never changes the parent chat. */
export function SubagentsSidePanel({ client, chatId, onClose, canManage = true, includeCompleted = false, store }: { client: NavinClient; chatId: string; onClose: () => void; canManage?: boolean; includeCompleted?: boolean; store?: SubagentCardStore }) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const [agents, setAgents] = useState<SubagentRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<SubagentHistoryEvent[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const [showCompleted, setShowCompleted] = useState(includeCompleted);
  const [notice, setNotice] = useState("");
  useEffect(() => { if (includeCompleted) setShowCompleted(true); }, [includeCompleted]);
  const cursor = useRef(0);
  const historyHasMore = useRef(false);
  const historyFinished = useRef(false);
  const epoch = useRef(0);
  const historyBusy = useRef(new Set<number>());
  const agent = agents.find((item) => item.task_id === selected);

  useEffect(() => {
    let live = true;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const rows: SubagentRecord[] = [];
        for (let offset = 0; ; offset += 200) {
          const page = await client.querySubagents<SubagentRecord[]>(chatId, "list", { offset, limit: 200 });
          rows.push(...page);
          if (page.length < 200 || !live) break;
        }
        if (live) {
          const unique = [...new Map(rows.map((row) => [row.task_id, row])).values()];
          setAgents(unique);
          setError("");
          if (store) reconcileCards(store, unique);
        }
      } catch (reason) { if (live) setError(String(reason instanceof Error ? reason.message : reason)); }
      finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { live = false; window.clearInterval(timer); };
  }, [client, chatId, store]);

  const readHistory = useCallback(async (taskId: string, generation: number, from: number) => {
    if (historyBusy.current.has(generation)) return;
    historyBusy.current.add(generation);
    setBusy(true);
    try {
      const page = await client.querySubagents<SubagentHistory>(chatId, "history", { task_id: taskId, after: from, limit: 100 });
      if (generation !== epoch.current) return;
      if (page.agent) setAgents((current) => [page.agent!, ...current.filter((item) => item.task_id !== taskId)]);
      setEvents((current) => {
        const merged = new Map((from === 0 ? [] : current).map((event) => [event.seq, event]));
        for (const event of page.events) merged.set(event.seq, event);
        return [...merged.values()].sort((a, b) => a.seq - b.seq);
      });
      cursor.current = page.next_cursor;
      historyHasMore.current = page.has_more;
      historyFinished.current = Boolean(page.agent?.done || page.agent?.phase === "interrupted");
      setMore(page.has_more);
      setError("");
    } catch (reason) { if (generation === epoch.current) setError(String(reason instanceof Error ? reason.message : reason)); }
    finally { historyBusy.current.delete(generation); if (generation === epoch.current) setBusy(false); }
  }, [chatId, client]);

  useEffect(() => {
    const generation = ++epoch.current;
    cursor.current = 0;
    historyHasMore.current = false;
    historyFinished.current = false;
    setEvents([]);
    setMore(false);
    setError("");
    setNotice("");
    if (!selected) return;
    // An old request may still be finishing after a quick selection change.
    const refresh = () => void readHistory(selected, generation, cursor.current);
    refresh();
    // Historical pages are explicit. Only poll the tail after the user has
    // reached it, while the selected agent still has work in flight.
    const timer = window.setInterval(() => {
      if (!historyHasMore.current && !historyFinished.current) refresh();
    }, 2000);
    return () => { ++epoch.current; window.clearInterval(timer); };
  }, [selected, readHistory]);

  const manage = async (operation: "recover" | "relaunch") => {
    if (!selected) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await client.querySubagents<{ detail?: string }>(chatId, operation, { task_id: selected });
      setError("");
      historyFinished.current = false;
      setNotice(result.detail || t("thread.subagents.accepted", { defaultValue: "Agent request accepted." }));
    }
    catch (reason) { setError(String(reason instanceof Error ? reason.message : reason)); }
    finally { setBusy(false); }
  };
  const filtered = agents.filter((item) => (showCompleted || (!item.done && !["interrupted", "interrupted_tool"].includes(item.phase))) && `${item.label} ${item.task_id} ${item.task_description || ""}`.toLowerCase().includes(query.toLowerCase()));

  return <motion.aside id="subagents-side-panel" aria-label={t("thread.subagents.title", { defaultValue: "Subagents" })} initial={reduced ? false : { opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: reduced ? 0 : 0.18 }} className="absolute inset-y-0 end-0 z-30 flex w-full flex-col border-s border-border/70 bg-background shadow-xl md:relative md:z-auto md:w-[min(390px,45%)] md:shrink-0 md:shadow-none" data-testid="subagents-side-panel">
    <div className="flex min-h-12 items-center gap-2 border-b border-border/60 px-3">
      {selected ? <Button variant="ghost" size="icon" onClick={() => setSelected(null)} aria-label={t("thread.subagents.back", { defaultValue: "Back to agents" })}><ArrowLeft className="h-4 w-4" /></Button> : <Users className="h-4 w-4 text-muted-foreground" />}
      <span className="min-w-0 flex-1 truncate text-sm font-medium">{selected ? agent?.label || selected : t("thread.subagents.title", { defaultValue: "Subagents" })}</span>
      <Button variant="ghost" size="icon" onClick={onClose} aria-label={t("thread.subagents.returnChat", { defaultValue: "Return to main chat" })}><X className="h-4 w-4" /></Button>
    </div>
    {error ? <div role="alert" className="px-4 py-2 text-sm text-destructive">{error}</div> : null}
    {notice ? <div role="status" className="px-4 py-2 text-sm text-muted-foreground">{notice}</div> : null}
    <AnimatePresence initial={false} mode="wait">
      {selected ? <motion.div key={selected} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : 0.12 }} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <div className="flex items-center gap-2">{agent ? <StateBadge state={subagentState(agent)} /> : null}<p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{[agent?.model, agent?.status_line || agent?.phase].filter(Boolean).join(" · ")}</p></div>
        <p className="mt-3 whitespace-pre-wrap break-words text-sm">{agent?.task_description}</p>
        {canManage && (agent?.recoverable || agent?.requires_relaunch) ? <Button variant="outline" size="sm" className="mt-3" disabled={busy} onClick={() => void manage(agent.recoverable ? "recover" : "relaunch")}>{agent.recoverable ? t("thread.subagents.resume", { defaultValue: "Resume agent" }) : t("thread.subagents.relaunch", { defaultValue: "Relaunch agent" })}</Button> : null}
        <ol className="mt-4 space-y-4" aria-label={t("thread.subagents.history", { defaultValue: "Agent conversation and actions" })}>
          {events.map((event) => <li key={event.seq} data-event-seq={event.seq} className="min-w-0 border-b border-border/40 pb-3">
            <div className="mb-1 flex items-center justify-between gap-2 text-xs text-muted-foreground"><span>{String(event.payload.name || event.payload.role || event.kind)}</span><time>{new Date(event.created_at * 1000).toLocaleTimeString()}</time></div>
            <EventBody event={event} />
          </li>)}
        </ol>
        {!events.length && !busy ? <p className="mt-4 text-sm text-muted-foreground">{t("thread.subagents.noHistory", { defaultValue: "No recorded actions yet." })}</p> : null}
        {busy ? <Loader2 className="mx-auto my-3 h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" /> : null}
        {more ? <Button variant="ghost" className="mt-3 w-full" disabled={busy} onClick={() => void readHistory(selected, epoch.current, cursor.current)}>{t("thread.subagents.moreActions", { defaultValue: "Load more actions" })}</Button> : null}
      </motion.div> : <motion.div key="list" className="flex min-h-0 flex-1 flex-col">
        <div className="space-y-2 px-3 py-3"><input aria-label={t("thread.subagents.search", { defaultValue: "Search agents" })} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("thread.subagents.search", { defaultValue: "Search agents" })} className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" /><label className="flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={showCompleted} onChange={(event) => setShowCompleted(event.target.checked)} />{t("thread.subagents.completedHistory", { defaultValue: "Include completed agents" })}</label></div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {filtered.map((item) => <button key={item.task_id} type="button" onClick={() => setSelected(item.task_id)} className="flex min-h-14 w-full flex-col justify-center rounded-md px-3 py-2 text-start hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><span className="flex w-full items-center gap-2"><span className="min-w-0 flex-1 truncate text-sm font-medium">{item.label}</span><StateBadge state={subagentState(item)} /></span><span className="mt-0.5 w-full truncate text-xs text-muted-foreground">{item.status_line || item.phase}</span></button>)}
          {!filtered.length ? <p className="px-3 py-4 text-sm text-muted-foreground">{t("thread.subagents.noneActive", { defaultValue: "No matching agents." })}</p> : null}
        </div>
      </motion.div>}
    </AnimatePresence>
  </motion.aside>;
}
