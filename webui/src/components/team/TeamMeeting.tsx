import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Mic, Square, Users } from "lucide-react";

import { TeamBotAvatar } from "@/components/team/TeamBotAvatar";
import type { ParallelSubagentCard } from "@/components/thread/ParallelSubagentsPanel";
import type { TeamFeedEntry, TeamIdentity } from "@/lib/team";
import { cn } from "@/lib/utils";

/** One spoken turn of a team meeting (a teammate or the user). */
export interface MeetingTurn {
  speaker: string;
  text: string;
}

export interface MeetingState {
  topic: string;
  /** Teammate names, in speaking order. */
  order: string[];
  /** Index in ``order`` of the teammate holding the floor. */
  index: number;
  round: number;
  rounds: number;
  /** Wall clock when the current turn was handed over (ms). */
  turnSince: number;
  transcript: MeetingTurn[];
  /** User remarks queued for the next speaker. */
  pendingUser: string[];
}

/** Characters of the transcript handed to each speaker. */
const TRANSCRIPT_CHARS = 6000;

/** The brief a teammate gets when it is its turn to speak. */
export function meetingTurnPrompt(state: MeetingState, speaker: string): string {
  const lines = state.transcript.map((turn) => `${turn.speaker}: ${turn.text}`);
  let transcript = lines.join("\n\n");
  if (transcript.length > TRANSCRIPT_CHARS) transcript = `...${transcript.slice(-TRANSCRIPT_CHARS)}`;
  const others = state.order.filter((name) => name !== speaker).join(", ");
  return [
    `Team meeting - round ${state.round}/${state.rounds}. You are ${speaker}; also present: ${others || "nobody"} and the user (the manager).`,
    `Topic: ${state.topic}`,
    transcript ? `What was said so far:\n${transcript}` : "You speak first.",
    "It is your turn to speak. Answer in a few sentences from your own expertise: agree, challenge or add what is missing, and address the others by name when you react to them. Do not edit files or run commands during the meeting: talk only.",
  ].join("\n\n");
}

/** How long a finished turn waits for its report in the feed before moving on. */
export const MEETING_REPORT_GRACE_MS = 4000;

/** The reply a teammate gave for the turn handed over at ``since``. */
export function meetingTurnReply(
  speaker: string,
  since: number,
  cards: ParallelSubagentCard[],
  feed: TeamFeedEntry[],
  now: number = Date.now(),
): { done: boolean; text: string } {
  const card = cards
    .filter((c) => c.member?.name === speaker && c.startedAt >= since - 250)
    .sort((a, b) => b.startedAt - a.startedAt)[0];
  if (!card || !card.done) return { done: false, text: "" };
  const result = [...feed].reverse().find((entry) => entry.kind === "result" && entry.taskId === card.taskId);
  // The done frame lands before the report: give the report a moment.
  if (!result && !card.error && now - (card.finishedAt ?? card.updatedAt) < MEETING_REPORT_GRACE_MS) {
    return { done: false, text: "" };
  }
  const text = (result?.content || card.error || card.statusLine || "").trim();
  return { done: true, text: text || "(no answer)" };
}

/** Moves the floor to the next speaker; null once every round is over. */
export function nextMeetingTurn(state: MeetingState, reply: MeetingTurn, now: number): MeetingState | null {
  const transcript = [...state.transcript, reply];
  let index = state.index + 1;
  let round = state.round;
  if (index >= state.order.length) {
    index = 0;
    round += 1;
  }
  if (round > state.rounds) return null;
  return { ...state, transcript, index, round, turnSince: now };
}

/**
 * A live team meeting: the user opens a topic, every teammate speaks in turn,
 * each one reading what the others said, and the user can step in between
 * turns. Turns run on the normal teammate engine (one assignment per turn).
 */
export function TeamMeeting({
  members,
  cards,
  feed,
  onAssign,
  onClose,
}: {
  members: TeamIdentity[];
  cards: ParallelSubagentCard[];
  feed: TeamFeedEntry[];
  onAssign: (target: string, content: string) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [chosen, setChosen] = useState<string[]>(() => members.map((m) => m.name));
  const [topic, setTopic] = useState("");
  const [rounds, setRounds] = useState(1);
  const [meeting, setMeeting] = useState<MeetingState | null>(null);
  const [ended, setEnded] = useState<MeetingTurn[] | null>(null);
  const [remark, setRemark] = useState("");
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const handed = useRef<string>("");
  const byName = useMemo(() => new Map(members.map((m) => [m.name, m])), [members]);

  const speaker = meeting ? meeting.order[meeting.index] : "";

  // Hand the floor over once per turn.
  useEffect(() => {
    if (!meeting) return;
    const key = `${meeting.round}:${meeting.index}`;
    if (handed.current === key) return;
    handed.current = key;
    const withUser = meeting.pendingUser.length
      ? { ...meeting, transcript: [...meeting.transcript, ...meeting.pendingUser.map((text) => ({ speaker: "User", text }))], pendingUser: [] }
      : meeting;
    if (withUser !== meeting) setMeeting(withUser);
    onAssign(speaker, meetingTurnPrompt(withUser, speaker)).catch((failure: unknown) => {
      setError(failure instanceof Error ? failure.message : String(failure));
      setEnded(withUser.transcript);
      setMeeting(null);
    });
  }, [meeting, onAssign, speaker]);

  // Wait for the speaker's answer, then pass the floor.
  useEffect(() => {
    if (!meeting) return;
    const reply = meetingTurnReply(speaker, meeting.turnSince, cards, feed);
    if (!reply.done) {
      // A finished turn still waiting for its report: look again shortly.
      const timer = window.setTimeout(() => setTick((n) => n + 1), 1000);
      return () => window.clearTimeout(timer);
    }
    const next = nextMeetingTurn(meeting, { speaker, text: reply.text }, Date.now());
    if (next) setMeeting(next);
    else {
      setEnded([...meeting.transcript, { speaker, text: reply.text }]);
      setMeeting(null);
    }
    return undefined;
  }, [cards, feed, meeting, speaker, tick]);

  const start = () => {
    const order = members.map((m) => m.name).filter((name) => chosen.includes(name));
    if (!topic.trim() || order.length === 0) return;
    setError("");
    setEnded(null);
    handed.current = "";
    setMeeting({ topic: topic.trim(), order, index: 0, round: 1, rounds, turnSince: Date.now(), transcript: [], pendingUser: [] });
  };

  const stop = () => {
    if (meeting) setEnded(meeting.transcript);
    setMeeting(null);
  };

  const addRemark = () => {
    const text = remark.trim();
    if (!text || !meeting) return;
    setMeeting({ ...meeting, pendingUser: [...meeting.pendingUser, text] });
    setRemark("");
  };

  const transcript = meeting ? meeting.transcript : ended ?? [];

  return (
    <section data-testid="team-meeting" className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border/50 px-3 py-1.5">
        <Users className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        <span className="flex-1 text-[12.5px] font-semibold text-foreground">
          {t("thread.team.meeting.title", { defaultValue: "Team meeting" })}
        </span>
        {meeting ? (
          <span className="text-[11px] text-muted-foreground">
            {t("thread.team.meeting.round", { defaultValue: "Round {{round}}/{{rounds}}", round: meeting.round, rounds: meeting.rounds })}
          </span>
        ) : null}
        <button
          type="button"
          onClick={meeting ? stop : onClose}
          className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11.5px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          {meeting ? <Square className="h-3 w-3" aria-hidden /> : null}
          {meeting
            ? t("thread.team.meeting.end", { defaultValue: "End meeting" })
            : t("thread.team.meeting.back", { defaultValue: "Back to the team" })}
        </button>
      </div>

      {/* Who sits at the table, the speaker highlighted. */}
      <div className="flex shrink-0 flex-wrap gap-1.5 border-b border-border/50 px-3 py-2">
        {members.map((member) => {
          const seated = chosen.includes(member.name);
          const talking = member.name === speaker;
          return (
            <button
              key={member.id}
              type="button"
              disabled={Boolean(meeting)}
              data-testid={`meeting-seat-${member.id}`}
              aria-pressed={seated}
              onClick={() =>
                setChosen((prev) => (prev.includes(member.name) ? prev.filter((n) => n !== member.name) : [...prev, member.name]))
              }
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full py-0.5 pe-2.5 ps-0.5 text-[12px] transition-colors",
                talking ? "bg-[#1d6af0]/12 text-foreground ring-1 ring-[#1d6af0]/50" : seated ? "bg-foreground/[0.06] text-foreground" : "text-muted-foreground/60",
              )}
            >
              <TeamBotAvatar avatar={member.avatar} color={member.color} photo={member.photo} seed={member.id} gender={member.gender} size={22} active={talking} />
              <span className="font-medium">{member.name}</span>
              {talking ? <Mic className="h-3 w-3 text-[#1d6af0]" aria-label={t("thread.team.meeting.speaking", { defaultValue: "speaking" })} /> : null}
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3 [scrollbar-width:thin]" aria-live="polite">
        {meeting || ended ? (
          <p className="text-[11.5px] text-muted-foreground">
            {t("thread.team.meeting.topic", { defaultValue: "Topic:" })} <span className="text-foreground/90">{meeting?.topic ?? topic}</span>
          </p>
        ) : null}
        {transcript.map((turn, index) => {
          const member = byName.get(turn.speaker);
          return (
            <div key={`${index}-${turn.speaker}`} className="flex gap-2" data-testid="meeting-turn">
              {member ? (
                <TeamBotAvatar avatar={member.avatar} color={member.color} photo={member.photo} seed={member.id} gender={member.gender} size={26} className="mt-0.5" />
              ) : (
                <span className="mt-0.5 grid h-[26px] w-[26px] shrink-0 place-items-center rounded-full bg-muted text-[10px] font-semibold">
                  {t("thread.team.meeting.you", { defaultValue: "You" })}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <span className="text-[11.5px] font-semibold text-foreground">{member?.name ?? t("thread.team.meeting.you", { defaultValue: "You" })}</span>
                <p className="mt-0.5 whitespace-pre-wrap break-words rounded-xl bg-muted/40 px-2.5 py-1.5 text-[12.5px] leading-relaxed text-foreground/90">
                  {turn.text}
                </p>
              </div>
            </div>
          );
        })}
        {meeting ? (
          <p className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {t("thread.team.meeting.waiting", { defaultValue: "{{name}} has the floor...", name: speaker })}
          </p>
        ) : null}
        {ended && !meeting ? (
          <p className="text-[11.5px] text-muted-foreground">{t("thread.team.meeting.ended", { defaultValue: "Meeting over." })}</p>
        ) : null}
        {error ? <p role="alert" className="text-[11.5px] text-[#c2410c]">{error}</p> : null}
      </div>

      <form
        className="shrink-0 border-t border-border/50 p-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (meeting) addRemark();
          else start();
        }}
      >
        <div className="flex items-end gap-1.5 rounded-xl border border-border/70 bg-background/80 p-1.5 focus-within:border-[#1d6af0]/60">
          <textarea
            value={meeting ? remark : topic}
            onChange={(event) => (meeting ? setRemark(event.target.value) : setTopic(event.target.value))}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            rows={2}
            maxLength={4000}
            placeholder={
              meeting
                ? t("thread.team.meeting.remarkPlaceholder", { defaultValue: "Speak up: the next teammate hears you before answering..." })
                : t("thread.team.meeting.topicPlaceholder", { defaultValue: "Meeting topic: what should the team discuss?" })
            }
            className="min-h-[2.5rem] flex-1 resize-none bg-transparent px-1 text-[12.5px] outline-none placeholder:text-muted-foreground/70"
          />
          {!meeting ? (
            <select
              value={rounds}
              onChange={(event) => setRounds(Number(event.target.value))}
              aria-label={t("thread.team.meeting.rounds", { defaultValue: "Rounds" })}
              className="h-8 rounded-lg border border-border/60 bg-background px-1 text-[12px]"
            >
              {[1, 2, 3].map((n) => (
                <option key={n} value={n}>
                  {t("thread.team.meeting.roundsOption", { defaultValue: "{{count}} round(s)", count: n })}
                </option>
              ))}
            </select>
          ) : null}
          <button
            type="submit"
            disabled={meeting ? !remark.trim() : !topic.trim() || chosen.length === 0}
            className="inline-flex h-8 shrink-0 items-center gap-1 rounded-lg bg-[#1d6af0] px-2.5 text-[12px] font-medium text-white transition-opacity disabled:opacity-40"
          >
            {meeting
              ? t("thread.team.meeting.speak", { defaultValue: "Speak" })
              : t("thread.team.meeting.start", { defaultValue: "Start meeting" })}
          </button>
        </div>
      </form>
    </section>
  );
}
