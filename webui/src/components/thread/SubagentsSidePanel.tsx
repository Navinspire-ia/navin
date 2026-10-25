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

/** The same event read as a chat: what the agent was told in a bubble, its replies and tools inline. */
function ChatEvent({ event }: { event: SubagentHistoryEvent }) {
  const p = event.payload;
  if (event.kind === "message" && p.role === "user" && typeof p.content === "string" && p.content.trim()) {
    return <div className="flex justify-end"><div className="max-w-[85%] rounded-2xl bg-muted px-4 py-2.5"><MarkdownText className="text-[14px]">{p.content}</MarkdownText></div></div>;
  }
  if (event.kind === "phase") {
    const line = String(p.status_line || p.phase || "");
    return line ? <p className="text-xs text-muted-foreground">{line}</p> : null;
  }
  return <EventBody event={event} />;
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

const HISTORY_PAGE = 100;
/** Chat reading loads the next page this close to the end, and follows the tail closer than that. */
const LOAD_AHEAD_PX = 400;
const FOLLOW_TAIL_PX = 240;

function nearEnd(element: HTMLElement | null, distance: number): boolean {
  return !element || element.scrollHeight - element.scrollTop - element.clientHeight < distance;
}

/**
 * One agent's whole work: every recorded page, then the live tail while it
 * still runs. ``panel`` pages on request; ``chat`` reads like the chat and
 * loads the next page as the reader nears the end.
 */
export function SubagentTranscript({ client, chatId, taskId, agent: known, canManage = true, onAgent, variant = "panel", className }: {
  client: NavinClient;
  chatId: string;
  taskId: string;
  agent?: SubagentRecord;
  canManage?: boolean;
  onAgent?: (agent: SubagentRecord) => void;
  variant?: "panel" | "chat";
  className?: string;
}) {
  const { t } = useTranslation();
  const chat = variant === "chat";
  const [agent, setAgent] = useState<SubagentRecord | undefined>(known);
  const [events, setEvents] = useState<SubagentHistoryEvent[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const cursor = useRef(0);
  const historyHasMore = useRef(false);
  const historyFinished = useRef(false);
  const epoch = useRef(0);
  const historyBusy = useRef(new Set<number>());
  const scrollRef = useRef<HTMLDivElement>(null);
  const onAgentRef = useRef(onAgent);
  onAgentRef.current = onAgent;
  useEffect(() => { if (known) setAgent(known); }, [known]);

  const readHistory = useCallback(async (generation: number, from: number) => {
    if (historyBusy.current.has(generation)) return;
    historyBusy.current.add(generation);
    setBusy(true);
    try {
      const page = await client.querySubagents<SubagentHistory>(chatId, "history", { task_id: taskId, after: from, limit: HISTORY_PAGE });
      if (generation !== epoch.current) return;
      if (page.agent) {
        setAgent(page.agent);
        onAgentRef.current?.(page.agent);
      }
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
  }, [chatId, client, taskId]);

  useEffect(() => {
    const generation = ++epoch.current;
    cursor.current = 0;
    historyHasMore.current = false;
    historyFinished.current = false;
    setEvents([]);
    setMore(false);
    setError("");
    setNotice("");
    // An old request may still be finishing after a quick selection change.
    const refresh = () => void readHistory(generation, cursor.current);
    refresh();
    // Historical pages are explicit. Only poll the tail after the reader has
    // reached it, while the agent still has work in flight.
    const timer = window.setInterval(() => {
      if (!historyHasMore.current && !historyFinished.current) refresh();
    }, 2000);
    return () => { ++epoch.current; window.clearInterval(timer); };
  }, [readHistory]);

  const loadNext = useCallback(() => {
    if (historyHasMore.current) void readHistory(epoch.current, cursor.current);
  }, [readHistory]);

  // Chat reading: the next page comes as the reader nears the end; once every
  // page is in, a reader already at the end follows what the agent does next.
  useEffect(() => {
    if (!chat) return;
    const element = scrollRef.current;
    if (more) {
      if (nearEnd(element, LOAD_AHEAD_PX)) loadNext();
      return;
    }
    if (element && nearEnd(element, FOLLOW_TAIL_PX)) element.scrollTop = element.scrollHeight;
  }, [chat, events, more, loadNext]);

  const manage = async (operation: "recover" | "relaunch") => {
    setBusy(true);
    setNotice("");
    try {
      const result = await client.querySubagents<{ detail?: string }>(chatId, operation, { task_id: taskId });
      setError("");
      historyFinished.current = false;
      setNotice(result.detail || t("thread.subagents.accepted", { defaultValue: "Agent request accepted." }));
    }
    catch (reason) { setError(String(reason instanceof Error ? reason.message : reason)); }
    finally { setBusy(false); }
  };

  const status = [agent?.model, agent?.status_line || agent?.phase].filter(Boolean).join(" · ");
  return <div ref={scrollRef} onScroll={chat ? () => { if (more && nearEnd(scrollRef.current, LOAD_AHEAD_PX)) loadNext(); } : undefined} className={cn("min-h-0 flex-1 overflow-y-auto overscroll-contain", className)} data-testid="subagent-transcript">
    <div className={chat ? "mx-auto w-full max-w-3xl px-3 py-4 sm:px-4 sm:py-6" : "px-4 py-3"}>
      {error ? <div role="alert" className="mb-2 text-sm text-destructive">{error}</div> : null}
      {notice ? <div role="status" className="mb-2 text-sm text-muted-foreground">{notice}</div> : null}
      <div className="flex items-center gap-2">{agent && !chat ? <StateBadge state={subagentState(agent)} /> : null}<p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{status}</p></div>
      {chat
        ? agent?.task_description
          ? <div className="mt-4 flex justify-end"><div className="max-w-[85%] rounded-2xl bg-muted px-4 py-2.5"><p className="mb-1 text-[11px] font-medium text-muted-foreground">{t("thread.subagents.detailsTask", { defaultValue: "Task" })}</p><p className="whitespace-pre-wrap break-words text-sm">{agent.task_description}</p></div></div>
          : null
        : <p className="mt-3 whitespace-pre-wrap break-words text-sm">{agent?.task_description}</p>}
      {canManage && agent && (agent.recoverable || agent.requires_relaunch) ? <Button variant="outline" size="sm" className="mt-3" disabled={busy} onClick={() => void manage(agent.recoverable ? "recover" : "relaunch")}>{agent.recoverable ? t("thread.subagents.resume", { defaultValue: "Resume agent" }) : t("thread.subagents.relaunch", { defaultValue: "Relaunch agent" })}</Button> : null}
      <ol className={chat ? "mt-5 space-y-4" : "mt-4 space-y-4"} aria-label={t("thread.subagents.history", { defaultValue: "Agent conversation and actions" })}>
        {events.map((event) => <li key={event.seq} data-event-seq={event.seq} className={chat ? "min-w-0" : "min-w-0 border-b border-border/40 pb-3"}>
          {chat
            ? <ChatEvent event={event} />
            : <>
              <div className="mb-1 flex items-center justify-between gap-2 text-xs text-muted-foreground"><span>{String(event.payload.name || event.payload.role || event.kind)}</span><time>{new Date(event.created_at * 1000).toLocaleTimeString()}</time></div>
              <EventBody event={event} />
            </>}
        </li>)}
      </ol>
      {!events.length && !busy ? <p className="mt-4 text-sm text-muted-foreground">{t("thread.subagents.noHistory", { defaultValue: "No recorded actions yet." })}</p> : null}
      {busy ? <Loader2 className="mx-auto my-3 h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" /> : null}
      {more ? <Button variant="ghost" className="mt-3 w-full" disabled={busy} onClick={() => void readHistory(epoch.current, cursor.current)}>{t("thread.subagents.moreActions", { defaultValue: "Load more actions" })}</Button> : null}
    </div>
  </div>;
}

/**
 * One agent's whole work in the chat column. It covers the main chat without
 * unmounting it: going back finds the conversation where it was.
 */
export function SubagentChatView({ client, chatId, agent, canManage = true, onBack, className }: {
  client: NavinClient;
  chatId: string;
  agent: SubagentRecord;
  canManage?: boolean;
  onBack: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const [current, setCurrent] = useState(agent);
  useEffect(() => setCurrent(agent), [agent]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !event.defaultPrevented) onBack(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);
  return <motion.section
    aria-label={t("thread.subagents.workTitle", { defaultValue: "Subagent work" })}
    data-testid="subagent-chat-view"
    initial={reduced ? false : { opacity: 0, y: 8 }}
    animate={{ opacity: 1, y: 0 }}
    exit={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
    transition={{ duration: reduced ? 0 : 0.18, ease: [0.22, 1, 0.36, 1] }}
    className={cn("absolute inset-0 z-10 flex flex-col bg-background", className)}
  >
    <div className="flex min-h-12 shrink-0 items-center gap-2 border-b border-border/60 px-2 sm:px-3">
      <Button variant="ghost" size="sm" onClick={onBack} className="h-9 shrink-0 gap-1.5 px-2"><ArrowLeft className="h-4 w-4 rtl:rotate-180" aria-hidden />{t("thread.subagents.returnChat", { defaultValue: "Return to main chat" })}</Button>
      <span className="min-w-0 flex-1 truncate text-sm font-medium" title={current.label || current.task_id}>{current.label || current.task_id}</span>
      <StateBadge state={subagentState(current)} />
    </div>
    <SubagentTranscript client={client} chatId={chatId} taskId={agent.task_id} agent={agent} canManage={canManage} onAgent={setCurrent} variant="chat" />
  </motion.section>;
}

/** A separate reading surface: opening an agent never changes the parent chat.
 * With ``onSelect`` the click goes to the parent (which shows the agent's work
 * in the chat) instead of opening the transcript inside the panel. */
export function SubagentsSidePanel({ client, chatId, onClose, canManage = true, includeCompleted = false, store, onSelect, selectedId = null, className }: { client: NavinClient; chatId: string; onClose: () => void; canManage?: boolean; includeCompleted?: boolean; store?: SubagentCardStore; onSelect?: (agent: SubagentRecord) => void; selectedId?: string | null; className?: string }) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const [agents, setAgents] = useState<SubagentRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [showCompleted, setShowCompleted] = useState(includeCompleted);
  useEffect(() => { if (includeCompleted) setShowCompleted(true); }, [includeCompleted]);
  const agent = agents.find((item) => item.task_id === selected);
  const onAgent = useCallback((record: SubagentRecord) => {
    setAgents((current) => [record, ...current.filter((item) => item.task_id !== record.task_id)]);
  }, []);

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

  const filtered = agents.filter((item) => (showCompleted || (!item.done && !["interrupted", "interrupted_tool"].includes(item.phase))) && `${item.label} ${item.task_id} ${item.task_description || ""}`.toLowerCase().includes(query.toLowerCase()));
  const pick = (item: SubagentRecord) => { if (onSelect) onSelect(item); else setSelected(item.task_id); };

  return <motion.aside id="subagents-side-panel" aria-label={t("thread.subagents.title", { defaultValue: "Subagents" })} initial={reduced ? false : { opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: reduced ? 0 : 0.18 }} className={cn("absolute inset-y-0 end-0 z-30 flex w-full flex-col border-s border-border/70 bg-background shadow-xl md:relative md:z-auto md:w-[min(390px,45%)] md:shrink-0 md:shadow-none", className)} data-testid="subagents-side-panel">
    <div className="flex min-h-12 items-center gap-2 border-b border-border/60 px-3">
      {selected ? <Button variant="ghost" size="icon" onClick={() => setSelected(null)} aria-label={t("thread.subagents.back", { defaultValue: "Back to agents" })}><ArrowLeft className="h-4 w-4 rtl:rotate-180" /></Button> : <Users className="h-4 w-4 text-muted-foreground" />}
      <span className="min-w-0 flex-1 truncate text-sm font-medium">{selected ? agent?.label || selected : t("thread.subagents.title", { defaultValue: "Subagents" })}</span>
      <Button variant="ghost" size="icon" onClick={onClose} aria-label={t("thread.subagents.returnChat", { defaultValue: "Return to main chat" })}><X className="h-4 w-4" /></Button>
    </div>
    {error ? <div role="alert" className="px-4 py-2 text-sm text-destructive">{error}</div> : null}
    <AnimatePresence initial={false} mode="wait">
      {selected ? <motion.div key={selected} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : 0.12 }} className="flex min-h-0 flex-1 flex-col">
        <SubagentTranscript client={client} chatId={chatId} taskId={selected} agent={agent} canManage={canManage} onAgent={onAgent} />
      </motion.div> : <motion.div key="list" className="flex min-h-0 flex-1 flex-col">
        <div className="space-y-2 px-3 py-3"><input aria-label={t("thread.subagents.search", { defaultValue: "Search agents" })} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("thread.subagents.search", { defaultValue: "Search agents" })} className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" /><label className="flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={showCompleted} onChange={(event) => setShowCompleted(event.target.checked)} />{t("thread.subagents.completedHistory", { defaultValue: "Include completed agents" })}</label>{onSelect ? <p className="text-xs text-muted-foreground">{t("thread.subagents.openHint", { defaultValue: "Select an agent to read all its work in the chat." })}</p> : null}</div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {filtered.map((item) => {
            const current = Boolean(onSelect) && item.task_id === selectedId;
            return <button key={item.task_id} type="button" onClick={() => pick(item)} aria-current={current ? "true" : undefined} className={cn("flex min-h-14 w-full flex-col justify-center rounded-md px-3 py-2 text-start transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", current && "bg-muted/60 hover:bg-muted/60")}><span className="flex w-full items-center gap-2"><span className="min-w-0 flex-1 truncate text-sm font-medium">{item.label}</span><StateBadge state={subagentState(item)} /></span><span className="mt-0.5 w-full truncate text-xs text-muted-foreground">{item.status_line || item.phase}</span></button>;
          })}
          {!filtered.length ? <p className="px-3 py-4 text-sm text-muted-foreground">{t("thread.subagents.noneActive", { defaultValue: "No matching agents." })}</p> : null}
        </div>
      </motion.div>}
    </AnimatePresence>
  </motion.aside>;
}
