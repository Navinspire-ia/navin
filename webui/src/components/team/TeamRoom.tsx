import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useTranslation } from "react-i18next";
import { ArrowRight, Check, CircleSlash, Loader2, Send, UserPlus, X } from "lucide-react";

import { TeamBotAvatar } from "@/components/team/TeamBotAvatar";
import {
  subagentStatusLine,
  subagentStepLabel,
  type ParallelSubagentCard,
  type SubagentCardStore,
} from "@/components/thread/ParallelSubagentsPanel";
import type { TeamFeedEntry, TeamIdentity } from "@/lib/team";
import { cn } from "@/lib/utils";

export interface TeamRoomTeam {
  id: string;
  name: string;
  members: TeamIdentity[];
}

/**
 * The Team view: the right half of a split chat while Collaboration Mode works.
 *
 * Top: one lane per teammate with its real model, live status and last steps.
 * Middle: the team's conversation (assignments, peer messages, results).
 * Bottom: the user steps in, briefing or interrupting a running teammate, or
 * giving a new task to an idle one.
 */
export function TeamRoom({
  team,
  feed,
  store,
  canManage,
  onSend,
  onAssign,
  onClose,
  className,
}: {
  team: TeamRoomTeam;
  feed: TeamFeedEntry[];
  store: SubagentCardStore;
  canManage: boolean;
  /** Message to a running teammate (delivered at its next step). */
  onSend: (target: string, content: string) => Promise<void>;
  /** New task for a teammate. */
  onAssign: (target: string, content: string) => Promise<void>;
  onClose: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const { cards } = useSyncExternalStore(store.subscribe, store.getSnapshot);

  // Finished cards leave the store after a moment; a lane keeps the last one
  // so the result stays readable next to the conversation.
  const lastCards = useRef(new Map<string, ParallelSubagentCard>());
  const lanes = useMemo(() => {
    for (const card of cards) {
      const name = card.member?.name;
      if (!name) continue;
      const previous = lastCards.current.get(name);
      if (!previous || previous.taskId === card.taskId || card.startedAt >= previous.startedAt) {
        lastCards.current.set(name, card);
      }
    }
    return team.members.map((member) => ({ member, card: lastCards.current.get(member.name) ?? null }));
  }, [cards, team.members]);

  const running = lanes.filter((lane) => lane.card && !lane.card.done).map((lane) => lane.member.name);
  const visibleLanes = lanes.filter((lane) => lane.card);
  const idle = lanes.filter((lane) => !lane.card);

  return (
    <aside
      className={cn(
        "flex h-full min-h-0 w-full flex-col border-s border-border/60 bg-card/50 backdrop-blur-xl",
        className,
      )}
      aria-label={t("thread.team.roomLabel", { defaultValue: "Team view" })}
      data-testid="team-room"
    >
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border/50 px-3">
        <span className="flex -space-x-1.5 rtl:space-x-reverse">
          {team.members.slice(0, 5).map((member) => (
            <TeamBotAvatar
              key={member.id}
              avatar={member.avatar}
              color={member.color}
              size={22}
              active={running.includes(member.name)}
              className="ring-2 ring-background"
            />
          ))}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-semibold text-foreground">
            {t("thread.team.roomTitle", { defaultValue: "Team {{name}}", name: team.name })}
          </span>
          <span className="block truncate text-[11px] text-muted-foreground">
            {running.length
              ? t("thread.team.working", { defaultValue: "{{count}} working", count: running.length })
              : t("thread.team.idle", { defaultValue: "Waiting for a task" })}
          </span>
        </span>
        <button
          type="button"
          onClick={onClose}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
          aria-label={t("thread.team.close", { defaultValue: "Close the Team view" })}
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </header>

      {visibleLanes.length ? (
        <div
          className={cn(
            "grid shrink-0 gap-2 border-b border-border/50 p-2",
            visibleLanes.length === 1 ? "grid-cols-1" : "grid-cols-2",
          )}
        >
          {visibleLanes.map(({ member, card }) => (
            <TeamLane key={member.id} member={member} card={card!} />
          ))}
        </div>
      ) : null}
      {idle.length ? (
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border/50 px-3 py-1.5">
          <span className="text-[11px] text-muted-foreground">
            {t("thread.team.available", { defaultValue: "Available:" })}
          </span>
          {idle.map(({ member }) => (
            <span key={member.id} className="inline-flex items-center gap-1 text-[11.5px] text-foreground/80">
              <TeamBotAvatar avatar={member.avatar} color={member.color} size={16} />
              {member.name}
            </span>
          ))}
        </div>
      ) : null}

      <TeamConversation feed={feed} />

      {canManage ? (
        <TeamComposer team={team} running={running} onSend={onSend} onAssign={onAssign} />
      ) : null}
    </aside>
  );
}

function TeamLane({ member, card }: { member: TeamIdentity; card: ParallelSubagentCard }) {
  const { t } = useTranslation();
  const failed = card.done && card.phase === "error";
  const steps = (card.steps ?? []).slice(-3);
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="min-w-0 rounded-xl border border-border/60 bg-background/70 p-2"
      style={{ borderTopColor: member.color, borderTopWidth: 2 }}
      data-testid="team-lane"
    >
      <div className="flex items-center gap-2">
        <TeamBotAvatar avatar={member.avatar} color={member.color} size={24} active={!card.done} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-[12.5px] font-semibold text-foreground">{member.name}</span>
            {card.done ? (
              failed ? (
                <CircleSlash className="h-3.5 w-3.5 shrink-0 text-[#c2410c]" aria-label={t("thread.team.stopped", { defaultValue: "Stopped" })} />
              ) : (
                <Check className="h-3.5 w-3.5 shrink-0 text-[#1f9d74]" aria-label={t("thread.team.done", { defaultValue: "Done" })} />
              )
            ) : (
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" aria-hidden />
            )}
          </div>
          <span className="block truncate text-[10.5px] text-muted-foreground" title={card.model}>
            {[member.title, card.model].filter(Boolean).join(" · ")}
          </span>
        </div>
      </div>
      <p className="mt-1.5 line-clamp-2 text-[11.5px] text-foreground/80">{subagentStatusLine(card, t)}</p>
      {steps.length ? (
        <ul className="mt-1 space-y-0.5">
          {steps.map((step, index) => (
            <li key={`${step.verb}-${index}`} className="truncate text-[10.5px] text-muted-foreground">
              {subagentStepLabel(step, t)}
            </li>
          ))}
        </ul>
      ) : null}
    </motion.div>
  );
}

function TeamConversation({ feed }: { feed: TeamFeedEntry[] }) {
  const { t, i18n } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = listRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [feed.length]);
  const time = useMemo(
    () => new Intl.DateTimeFormat(i18n.language, { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    [i18n.language],
  );
  return (
    <div
      ref={listRef}
      onScroll={(event) => {
        const el = event.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
      className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3 py-3 [scrollbar-width:thin]"
      aria-live="polite"
    >
      {feed.length === 0 ? (
        <p className="mx-auto mt-6 max-w-[18rem] text-center text-[12px] text-muted-foreground">
          {t("thread.team.emptyFeed", {
            defaultValue: "Call a teammate by name in the chat (\"Mark, build the login page\") or give one a task below. Their exchanges appear here live.",
          })}
        </p>
      ) : null}
      <AnimatePresence initial={false}>
        {feed.map((entry) => (
          <motion.div
            key={entry.entryId}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18 }}
            className="flex gap-2"
          >
            <TeamBotAvatar avatar={entry.sender.avatar} color={entry.sender.color} size={26} className="mt-0.5" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-1.5 text-[11.5px]">
                <span className="font-semibold text-foreground">{entry.sender.name}</span>
                {entry.target ? (
                  <>
                    <ArrowRight className="h-3 w-3 text-muted-foreground rtl:-scale-x-100" aria-hidden />
                    <span className="font-medium text-foreground/80">{entry.target.name}</span>
                  </>
                ) : null}
                <EntryBadge entry={entry} />
                <span className="ms-auto tabular-nums text-[10.5px] text-muted-foreground/80">
                  {time.format(new Date(entry.ts * 1000))}
                </span>
              </div>
              <EntryBody entry={entry} />
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function EntryBadge({ entry }: { entry: TeamFeedEntry }) {
  const { t } = useTranslation();
  const label =
    entry.kind === "assign"
      ? entry.status === "queued"
        ? t("thread.team.kind.queued", { defaultValue: "task queued" })
        : t("thread.team.kind.assign", { defaultValue: "task" })
      : entry.kind === "result"
        ? entry.status === "ok"
          ? t("thread.team.kind.done", { defaultValue: "report" })
          : t("thread.team.kind.failed", { defaultValue: "stopped" })
        : entry.kind === "email"
          ? t("thread.team.kind.email", { defaultValue: "email sent" })
        : entry.kind === "user"
          ? t("thread.team.kind.user", { defaultValue: "interrupts" })
          : entry.status
            ? t(`thread.team.intent.${entry.status}`, { defaultValue: entry.status })
            : null;
  if (!label) return null;
  const tone =
    entry.kind === "result" && entry.status !== "ok"
      ? "bg-[#c2410c]/10 text-[#c2410c]"
      : entry.kind === "result"
        ? "bg-[#1f9d74]/10 text-[#1f9d74]"
        : entry.kind === "email"
          ? "bg-[#6d5bd0]/10 text-[#6d5bd0] dark:text-[#a99cf0]"
        : entry.kind === "user" || entry.status === "challenge"
          ? "bg-[#d4892a]/12 text-[#a15c07] dark:text-[#f0b35a]"
          : entry.status === "agree"
            ? "bg-[#1f9d74]/10 text-[#1f9d74]"
            : entry.status === "question"
              ? "bg-[#6d5bd0]/10 text-[#6d5bd0] dark:text-[#a99cf0]"
              : "bg-[#2f7de1]/10 text-[#2f7de1]";
  return <span className={cn("rounded-full px-1.5 py-px text-[10px] font-medium", tone)}>{label}</span>;
}

function EntryBody({ entry }: { entry: TeamFeedEntry }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const long = entry.content.length > 420;
  const text = long && !open ? `${entry.content.slice(0, 420).trimEnd()}...` : entry.content;
  return (
    <div
      className={cn(
        "mt-0.5 whitespace-pre-wrap break-words rounded-xl px-2.5 py-1.5 text-[12.5px] leading-relaxed text-foreground/90",
        entry.kind === "result" ? "bg-muted/55" : "bg-muted/30",
      )}
    >
      {text}
      {long ? (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="ms-1 text-[11.5px] font-medium text-[#2f7de1] hover:underline"
        >
          {open
            ? t("thread.team.showLess", { defaultValue: "Show less" })
            : t("thread.team.showMore", { defaultValue: "Show more" })}
        </button>
      ) : null}
    </div>
  );
}

function TeamComposer({
  team,
  running,
  onSend,
  onAssign,
}: {
  team: TeamRoomTeam;
  running: string[];
  onSend: (target: string, content: string) => Promise<void>;
  onAssign: (target: string, content: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [target, setTarget] = useState(team.members[0]?.name ?? "");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!team.members.some((member) => member.name === target)) setTarget(team.members[0]?.name ?? "");
  }, [team.members, target]);
  const isRunning = running.includes(target);
  const submit = async () => {
    const content = text.trim();
    if (!content || !target || busy) return;
    setBusy(true);
    setError("");
    try {
      if (isRunning) await onSend(target, content);
      else await onAssign(target, content);
      setText("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="shrink-0 border-t border-border/50 p-2"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="mb-1.5 flex flex-wrap gap-1">
        {team.members.map((member) => (
          <button
            key={member.id}
            type="button"
            onClick={() => setTarget(member.name)}
            aria-pressed={target === member.name}
            className={cn(
              "inline-flex h-6 items-center gap-1 rounded-full pe-2 ps-0.5 text-[11.5px] transition-colors",
              target === member.name
                ? "bg-foreground/[0.08] text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <TeamBotAvatar avatar={member.avatar} color={member.color} size={18} active={running.includes(member.name)} />
            {member.name}
          </button>
        ))}
      </div>
      <div className="flex items-end gap-1.5 rounded-xl border border-border/70 bg-background/80 p-1.5 focus-within:border-[#1d6af0]/60">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }}
          rows={2}
          maxLength={8000}
          placeholder={
            isRunning
              ? t("thread.team.interruptPlaceholder", {
                  defaultValue: "Interrupt {{name}}: explain the project, a problem, a change...",
                  name: target,
                })
              : t("thread.team.assignPlaceholder", { defaultValue: "Give {{name}} a task...", name: target })
          }
          className="min-h-[2.5rem] flex-1 resize-none bg-transparent px-1 text-[12.5px] outline-none placeholder:text-muted-foreground/70"
        />
        <button
          type="submit"
          disabled={busy || !text.trim() || !target}
          className="inline-flex h-8 shrink-0 items-center gap-1 rounded-lg bg-[#1d6af0] px-2.5 text-[12px] font-medium text-white transition-opacity disabled:opacity-40"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : isRunning ? (
            <Send className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <UserPlus className="h-3.5 w-3.5" aria-hidden />
          )}
          {isRunning
            ? t("thread.team.send", { defaultValue: "Send" })
            : t("thread.team.assign", { defaultValue: "Assign" })}
        </button>
      </div>
      {error ? <p className="mt-1 text-[11.5px] text-[#c2410c]" role="alert">{error}</p> : null}
    </form>
  );
}
