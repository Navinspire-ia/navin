import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, CircleDot, Hammer, Loader2, Pause, Play, Square, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { preloadMarkdownText } from "@/components/MarkdownText";
import {
  fetchBoard,
  updateBoard,
  type SessionPlan,
  type SessionPlanItem,
  type SessionPlanQuality,
} from "@/lib/api";
import { requestOpenSessionPlan } from "@/lib/workbench-events";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

/** Safety net for the case where a board_updated broadcast is missed. */
const POLL_MS = 20_000;

/**
 * P2-7: what the pre-Build quality badge should show.
 *
 * The judge only speaks while Build is actionable - mid-run or on a finished
 * plan its verdict is history, not advice. Older gateways send no `quality`
 * field at all, which must render nothing rather than a false "ready".
 */
export function planQualityVerdict(
  quality: SessionPlanQuality | null | undefined,
  canBuild: boolean,
): "hidden" | "ready" | "gaps" {
  if (!canBuild || !quality) return "hidden";
  return quality.status === "gaps" && quality.gaps.length > 0 ? "gaps" : "ready";
}

/**
 * Whether Build must stay held because the plan has unreviewed gaps.
 *
 * The verdict alone was decoration: it named what the plan was missing while
 * Build stayed one click away, so a plan with no acceptance criteria could be
 * handed to the builder by accident. Holding the button makes the warning
 * mean something, and acknowledging it is the deliberate way past - the judge
 * is a heuristic, so it must never be able to strand a plan for good.
 */
export function buildHeldByPlanGaps(
  quality: SessionPlanQuality | null | undefined,
  canBuild: boolean,
  gapsAcknowledged: boolean,
): boolean {
  return planQualityVerdict(quality, canBuild) === "gaps" && !gapsAcknowledged;
}

export type PlanItemTone =
  | "done"
  | "active"
  | "planned"
  | "review"
  | "fix"
  | "blocked"
  | "cancelled"
  | "pending";

/** Status color for a checklist row - same family as composer modes, kept quiet. */
export function planItemTone(
  item: Pick<SessionPlanItem, "done" | "blocked" | "cancelled" | "status" | "active">,
  current: boolean,
): PlanItemTone {
  if (item.cancelled) return "cancelled";
  if (item.done) return "done";
  if (item.blocked) return "blocked";
  if (item.status === "fix") return "fix";
  if (item.status === "review" || item.status === "audit") return "review";
  if (current || item.active || item.status === "in_progress") return "active";
  if (item.status === "planned") return "planned";
  return "pending";
}

const PLAN_ROW_TONE_CLASS: Record<PlanItemTone, string> = {
  done: "text-emerald-700/75 line-through dark:text-emerald-400/70",
  active: "text-[hsl(var(--composer-plan-fg))] dark:text-[hsl(var(--composer-plan))]",
  planned: "text-[hsl(var(--composer-ask-fg))] dark:text-[hsl(var(--composer-ask))]/80",
  review: "text-[hsl(var(--composer-review-fg))] dark:text-[hsl(var(--composer-review))]",
  fix: "text-[hsl(var(--composer-security-fg))] dark:text-[hsl(var(--composer-security))]",
  blocked: "text-amber-700 dark:text-amber-400/90",
  cancelled: "text-muted-foreground/55 line-through",
  pending: "text-foreground/72",
};

const PLAN_ROW_SURFACE_CLASS: Record<PlanItemTone, string> = {
  done: "",
  active: "bg-[hsl(var(--composer-plan-soft))] dark:bg-[hsl(var(--composer-plan))]/10",
  planned: "",
  review: "bg-[hsl(var(--composer-review-soft))] dark:bg-[hsl(var(--composer-review))]/10",
  fix: "bg-[hsl(var(--composer-security-soft))] dark:bg-[hsl(var(--composer-security))]/10",
  blocked: "bg-amber-500/10",
  cancelled: "",
  pending: "",
};

/** Whether the chat panel can pause or resume the mission ledger. */
export function missionControlState(
  plan: Pick<SessionPlan, "ledger_status" | "version"> | null,
  isStreaming: boolean,
): { canPause: boolean; canResume: boolean } {
  const status = plan?.ledger_status || "";
  const live = Boolean(plan?.version) && status !== "" && status !== "done" && status !== "failed";
  if (!live || isStreaming) return { canPause: false, canResume: false };
  if (status === "paused") return { canPause: false, canResume: true };
  return { canPause: true, canResume: false };
}

/**
 * Small flat Plan card in the chat transcript (Cursor-style checklist).
 * No heavy shadow / 3D chrome - just a bordered surface with steps.
 */
export function ThreadPlanPanel({
  sessionKey,
  activity = null,
  isStreaming = false,
  onBuild,
  onStop,
}: {
  sessionKey: string | null;
  /** Latest live tool action (e.g. "exec kubectl get pods"), shown under the
   * current item so the plan says what is really happening, not just a title. */
  activity?: string | null;
  isStreaming?: boolean;
  /** Kick off Build mode (``/forge``) for the current plan. */
  onBuild?: (plan: SessionPlan) => void;
  /** Cancel the active turn (and park plan steps server-side). */
  onStop?: () => void;
}) {
  const { client, token } = useClient();
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [plan, setPlan] = useState<SessionPlan | null>(null);
  const [expanded, setExpanded] = useState(true);
  // A finished plan is a record, so it stays in the transcript where it was
  // built - anchored to its turn, scrolling away with it. Nothing expires it
  // on a timer; the close button is how it goes.
  const [hidden, setHidden] = useState(false);
  // The gap warning used to be decoration: it named what the plan was missing
  // while Build stayed one click away. Acknowledging is now the only way past
  // it, and it unlocks this exact revision - a replan has to be judged again.
  const [gapsAcknowledged, setGapsAcknowledged] = useState(false);
  const [missionBusy, setMissionBusy] = useState(false);

  const listOpen = expanded;

  const load = useCallback(async () => {
    if (!token || !sessionKey) return;
    try {
      const board = await fetchBoard(token, sessionKey);
      setPlan(board.session_plan ?? null);
    } catch {
      // A plan that cannot be read is not worth an error in the chat; the
      // board tab reports board failures already.
    }
  }, [token, sessionKey]);

  useEffect(() => {
    setPlan(null);
    setHidden(false);
    void load();
  }, [load]);

  useEffect(() => {
    const unsubscribe = client.onBoardUpdate(() => void load());
    const interval = window.setInterval(() => void load(), POLL_MS);
    return () => {
      unsubscribe();
      window.clearInterval(interval);
    };
  }, [client, load]);

  // A finished plan collapses to its one-line summary: still there as a record
  // of the turn, without a wall of ticked rows in the middle of the thread.
  const planComplete = Boolean(plan?.complete);
  useEffect(() => {
    if (planComplete) setExpanded(false);
  }, [planComplete]);

  const qualitySignature = plan?.quality
    ? [
        plan.version ?? 0,
        plan.quality.status,
        plan.quality.gaps.map((gap) => `${gap.kind}/${gap.task_id ?? ""}`).join(","),
      ].join("|")
    : "";
  useEffect(() => {
    setGapsAcknowledged(false);
  }, [qualitySignature]);

  // Keep the checklist visible while the agent works so status colors (done /
  // current / pending) stay readable. Collapse only when the plan is complete.
  const wasStreamingRef = useRef(isStreaming);
  useEffect(() => {
    if (wasStreamingRef.current === isStreaming) return;
    wasStreamingRef.current = isStreaming;
    if (!isStreaming && plan && !plan.complete) {
      setExpanded(true);
    }
  }, [isStreaming, plan]);

  const currentItem = useMemo(() => {
    if (!plan || plan.complete) return null;
    return plan.items.find((item) => item.id === plan.current_id && !item.cancelled) ?? null;
  }, [plan]);

  const focusTitle =
    currentItem?.title ||
    plan?.goal ||
    plan?.title ||
    tx("thread.plan.created", "Plan");

  const applyMissionAction = useCallback(
    async (action: "pause_mission" | "resume_mission") => {
      if (!token || !sessionKey || missionBusy) return;
      setMissionBusy(true);
      try {
        await updateBoard(
          token,
          sessionKey,
          action === "pause_mission"
            ? { action: "pause_mission", reason: "human" }
            : { action: "resume_mission" },
        );
        await load();
      } catch {
        // The board tab already surfaces API failures; keep the chat card quiet.
      } finally {
        setMissionBusy(false);
      }
    },
    [load, missionBusy, sessionKey, token],
  );

  // No session plan means this chat never touched the board. Do not fall
  // back to the project-wide task list: that board lives in
  // `.navin/board/` and is shared by every chat of the project, so a new
  // or other thread would inherit someone else's 12/33 TASKS card.
  if (!plan || plan.items.length === 0) {
    return null;
  }
  const hasRemaining = plan.items.some((item) => !item.done);
  const started = plan.items.some(
    (item) => item.done || item.active || item.cancelled,
  );
  // Build is the idle-state call to action: any plan with steps left can be
  // (re)launched, including one interrupted by /stop or a restart. Stop only
  // makes sense while a run is actually live - a leftover "active" step from
  // a dead run must not keep a Stop button (and a spinner) on screen forever.
  const canBuild = Boolean(onBuild) && !isStreaming && !plan.complete && hasRemaining;
  const canStop = Boolean(onStop) && isStreaming;
  const { canPause: canPauseMission, canResume: canResumeMission } = missionControlState(
    plan,
    isStreaming,
  );
  // Build is held, not hidden: the gaps are recoverable (add the missing
  // acceptance criteria and the judge clears them), and the user can always
  // override from the warning itself. Hiding the button would strand a plan
  // the judge scores wrongly.
  const buildHeldByGaps = buildHeldByPlanGaps(plan.quality, canBuild, gapsAcknowledged);
  const buildHeldTitle = tx(
    "thread.plan.qualityHoldHint",
    "This plan has gaps. Review them, or use the link in the warning below to start anyway.",
  );
  const buildLabel = started
    ? tx("thread.plan.resume", "Resume")
    : tx("thread.plan.build", "Build");
  const gapsCardId = "thread-plan-gaps";
  const showCard = !hidden;

  return (
      <AnimatePresence initial={false}>
        {showCard ? (
        <motion.div
          key="plan-card"
          data-testid="thread-plan-panel"
          initial={reduceMotion ? false : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          // Out through the top: the card is leaving the conversation, so it
          // pulls up and away rather than sinking back onto the composer.
          exit={
            reduceMotion
              ? { opacity: 0 }
              : { opacity: 0, y: -14, height: 0, marginTop: 0 }
          }
          transition={{ type: "spring", duration: 0.3, bounce: 0 }}
          className="mt-2 w-full min-w-0"
        >
          <div
            className={cn(
              "overflow-hidden rounded-xl border border-border/55",
              "bg-muted/35 dark:bg-muted/25",
            )}
          >
            <div className="flex items-center gap-2 px-3 pt-2.5 pb-1">
              <button
                type="button"
                onClick={() => setExpanded((open) => !open)}
                aria-expanded={listOpen}
                className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
              >
                <ChevronDown
                  aria-hidden
                  className={cn(
                    "h-3 w-3 shrink-0 text-muted-foreground/55 transition-transform duration-200",
                    !listOpen && "-rotate-90",
                  )}
                />
                <span className="min-w-0 flex-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                  {plan.complete
                    ? tx("thread.plan.complete", "Plan complete")
                    : tx("thread.plan.created", "Plan")}
                  {plan.version != null ? (
                    <span className="ml-1.5 font-medium normal-case tracking-normal text-muted-foreground/60">
                      v{plan.version}
                      {plan.ledger_status ? ` · ${plan.ledger_status}` : ""}
                    </span>
                  ) : null}
                </span>
                <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/70">
                  {plan.done_count}/{plan.total_count}
                </span>
              </button>
              <div className="flex shrink-0 items-center gap-0.5">
                <button
                  type="button"
                  data-testid="thread-plan-view"
                  onClick={() => {
                    preloadMarkdownText();
                    requestOpenSessionPlan();
                  }}
                  className={cn(
                    "rounded-md px-2 py-1 text-[11.5px] font-medium text-muted-foreground/90",
                    "transition-colors hover:bg-background/50 hover:text-foreground",
                  )}
                >
                  {tx("thread.plan.view", "View plan")}
                </button>
                <button
                  type="button"
                  data-testid="thread-plan-hide"
                  onClick={() => setHidden(true)}
                  title={tx("thread.plan.hide", "Hide plan")}
                  aria-label={tx("thread.plan.hide", "Hide plan")}
                  className={cn(
                    "grid h-6 w-6 shrink-0 place-items-center rounded-md",
                    "text-muted-foreground/70",
                    "transition-colors hover:bg-background/50 hover:text-foreground",
                  )}
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
                {canPauseMission ? (
                  <button
                    type="button"
                    data-testid="thread-plan-pause-mission"
                    disabled={missionBusy}
                    onClick={() => void applyMissionAction("pause_mission")}
                    title={tx("thread.plan.pauseMission", "Pause mission")}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md px-2 py-1",
                      "text-[11.5px] font-medium text-muted-foreground/90",
                      "transition-colors hover:bg-background/50 hover:text-foreground",
                      "disabled:cursor-not-allowed disabled:opacity-45",
                    )}
                  >
                    <Pause className="h-2.5 w-2.5 fill-current" aria-hidden />
                    {tx("thread.plan.pauseMission", "Pause")}
                  </button>
                ) : null}
                {canResumeMission ? (
                  <button
                    type="button"
                    data-testid="thread-plan-resume-mission"
                    disabled={missionBusy}
                    onClick={() => void applyMissionAction("resume_mission")}
                    title={tx("thread.plan.resumeMission", "Resume mission")}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md px-2 py-1",
                      "text-[11.5px] font-medium text-muted-foreground/90",
                      "transition-colors hover:bg-background/50 hover:text-foreground",
                      "disabled:cursor-not-allowed disabled:opacity-45",
                    )}
                  >
                    <Play className="h-2.5 w-2.5 fill-current" aria-hidden />
                    {tx("thread.plan.resumeMission", "Resume mission")}
                  </button>
                ) : null}
                {canStop ? (
                  <button
                    type="button"
                    data-testid="thread-plan-stop"
                    onClick={() => onStop?.()}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md px-2 py-1",
                      "text-[11.5px] font-medium text-muted-foreground/90",
                      "transition-colors hover:bg-background/50 hover:text-foreground",
                    )}
                  >
                    <Square className="h-2.5 w-2.5 fill-current" aria-hidden />
                    {tx("thread.plan.stop", "Stop")}
                  </button>
                ) : null}
                {canBuild ? (
                  <>
                    <button
                      type="button"
                      data-testid="thread-plan-skip"
                      onClick={() => setExpanded(false)}
                      className={cn(
                        "rounded-md px-2 py-1 text-[11.5px] font-medium text-muted-foreground/90",
                        "transition-colors hover:bg-background/50 hover:text-foreground",
                      )}
                    >
                      {tx("thread.plan.skip", "Skip")}
                    </button>
                    <button
                      type="button"
                      data-testid="thread-plan-build"
                      onClick={() => onBuild?.(plan)}
                      disabled={buildHeldByGaps}
                      title={buildHeldByGaps ? buildHeldTitle : undefined}
                      aria-describedby={buildHeldByGaps ? gapsCardId : undefined}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-md px-2 py-1",
                        "bg-foreground text-[11.5px] font-semibold text-background",
                        "transition-transform active:scale-[0.96] hover:bg-foreground/90",
                        "disabled:cursor-not-allowed disabled:opacity-45",
                        "disabled:hover:bg-foreground disabled:active:scale-100",
                      )}
                    >
                      <Hammer className="h-3 w-3" aria-hidden />
                      {buildLabel}
                    </button>
                  </>
                ) : null}
              </div>
            </div>

            <div className="px-3 pb-2 pt-0.5">
              <p className="truncate text-[13px] font-semibold leading-snug text-foreground/92">
                {focusTitle}
              </p>
            </div>

            {plan.stall_count != null && plan.stall_count > 0 ? (
              <div className="px-3 pb-1">
                <span className="inline-flex rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-400">
                  {t("thread.planStall", {
                    defaultValue: "stalled x{{count}}",
                    count: plan.stall_count,
                  })}
                </span>
              </div>
            ) : null}
            {plan.loop_detected ? (
              <div className="px-3 pb-1">
                <span className="inline-flex rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] font-medium text-rose-700 dark:text-rose-400">
                  {tx("thread.plan.loop", "loop detected")}
                </span>
              </div>
            ) : null}
            {(plan.token_budget ?? 0) > 0 ? (
              <div className="px-3 pb-1">
                <span className="inline-flex rounded bg-background/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  {t("thread.plan.budget", {
                    defaultValue: "budget {{used}}/{{max}} tokens",
                    used: plan.tokens_used ?? 0,
                    max: plan.token_budget,
                  })}
                </span>
              </div>
            ) : null}
            {plan.pause_reason ? (
              <div className="px-3 pb-1">
                <span className="inline-flex rounded bg-background/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  {t("thread.planPaused", { defaultValue: "paused" })}
                  {plan.pause_reason !== "manual" ? ` · ${plan.pause_reason}` : ""}
                </span>
              </div>
            ) : null}
            {/* P2-7: pre-Build quality judge. Shown while Build is clickable so
                a plan with no acceptance criteria warns before the handoff. */}
            {planQualityVerdict(plan.quality, canBuild) !== "hidden" && plan.quality ? (
              planQualityVerdict(plan.quality, canBuild) === "ready" ? (
                <div className="px-3 pb-1">
                  <span
                    data-testid="thread-plan-quality-ready"
                    className="inline-flex rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400"
                  >
                    {tx("thread.plan.qualityReady", "Plan ready")}
                  </span>
                </div>
              ) : (
                <div className="px-3 pb-1.5">
                  <div
                    id={gapsCardId}
                    data-testid="thread-plan-quality-gaps"
                    className="inline-flex max-w-full flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1"
                  >
                    <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
                      {t("thread.plan.qualityGaps", {
                        defaultValue: "Plan gaps ({{count}})",
                        count: plan.quality.gaps.length,
                      })}
                    </span>
                    {plan.quality.gaps.slice(0, 3).map((gap, index) => (
                      <span
                        key={`${gap.kind}-${gap.task_id ?? index}`}
                        className="min-w-0 text-[10.5px] leading-snug text-amber-800/90 dark:text-amber-300/90"
                      >
                        {gap.message}
                      </span>
                    ))}
                    {plan.quality.gaps.length > 3 ? (
                      <span className="text-[10.5px] text-amber-800/70 dark:text-amber-300/70">
                        {t("thread.plan.qualityMore", {
                          defaultValue: "+{{count}} more",
                          count: plan.quality.gaps.length - 3,
                        })}
                      </span>
                    ) : null}
                    {buildHeldByGaps ? (
                      <button
                        type="button"
                        data-testid="thread-plan-build-anyway"
                        onClick={() => {
                          setGapsAcknowledged(true);
                          onBuild?.(plan);
                        }}
                        className={cn(
                          "rounded px-0.5 text-[10.5px] font-semibold",
                          "text-amber-800 underline underline-offset-2 dark:text-amber-300",
                          "transition-colors hover:bg-amber-500/20",
                        )}
                      >
                        {started
                          ? tx("thread.plan.qualityResumeAnyway", "Resume anyway")
                          : tx("thread.plan.qualityBuildAnyway", "Build anyway")}
                      </button>
                    ) : null}
                  </div>
                </div>
              )
            ) : null}
            {isStreaming && activity ? (
              <div className="flex min-w-0 items-center gap-1.5 px-3 pb-1.5 text-[11px] text-muted-foreground/80">
                <span
                  className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-sky-500/80"
                  aria-hidden
                />
                <span className="shrink-0 font-medium text-muted-foreground/90">
                  {tx("thread.plan.now", "Now")}
                </span>
                <span className="min-w-0 truncate font-mono">{activity}</span>
              </div>
            ) : null}

            {listOpen ? (
              <ul className="max-h-56 space-y-0 overflow-y-auto border-t border-border/40 px-2.5 py-1.5 scrollbar-thin scrollbar-track-transparent">
                {plan.last_change?.reason ? (
                  <li className="px-0.5 pb-1 text-[10.5px] text-muted-foreground/65">
                    {tx("thread.plan.lastChange", "Last change")}: v
                    {plan.last_change.version ?? plan.version} -{" "}
                    {plan.last_change.reason}
                  </li>
                ) : null}
                {plan.items.map((item) => (
                  <PlanRow
                    key={item.id}
                    item={item}
                    current={
                      item.id === plan.current_id &&
                      !plan.complete &&
                      !item.cancelled
                    }
                    running={isStreaming}
                    activity={
                      item.id === plan.current_id && !plan.complete
                        ? activity
                        : null
                    }
                    cancelledLabel={tx("thread.plan.cancelled", "Cancelled")}
                  />
                ))}
              </ul>
            ) : null}
          </div>
        </motion.div>
        ) : null}
      </AnimatePresence>
  );
}

function PlanRow({
  item,
  current,
  running = false,
  activity,
  cancelledLabel,
}: {
  item: SessionPlanItem;
  current: boolean;
  /** A live run is in flight: only then may the current row spin. */
  running?: boolean;
  activity?: string | null;
  cancelledLabel: string;
}) {
  const cancelled = Boolean(item.cancelled);
  const tone = planItemTone(item, current);
  return (
    <li
      data-testid="thread-plan-row"
      data-tone={tone}
      className={cn(
        "flex min-w-0 items-start gap-2 rounded-md px-1.5 py-1 text-[12px] leading-[1.45]",
        PLAN_ROW_SURFACE_CLASS[tone],
      )}
    >
      <span className="flex h-4 w-3.5 shrink-0 items-start justify-center pt-0.5">
        <PlanMarker current={current} running={running} tone={tone} />
      </span>
      <span className="min-w-0 flex-1">
        <span className={cn("flex min-w-0 items-start gap-1 break-words", PLAN_ROW_TONE_CLASS[tone])}>
          <span className="min-w-0 flex-1">{item.title}</span>
          {cancelled ? (
            <span className="ml-1 shrink-0 text-[10.5px] font-medium text-muted-foreground/60">
              {cancelledLabel}
            </span>
          ) : null}
        </span>
        {current && activity && !cancelled ? (
          <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px] text-[hsl(var(--composer-plan-fg))] dark:text-[hsl(var(--composer-plan))]/80">
            <span
              className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[hsl(var(--composer-plan))]"
              aria-hidden
            />
            <span className="min-w-0 truncate font-mono">{activity}</span>
          </span>
        ) : null}
      </span>
    </li>
  );
}

function PlanMarker({
  current,
  running = false,
  tone,
}: {
  current: boolean;
  running?: boolean;
  tone: PlanItemTone;
}) {
  if (tone === "cancelled") {
    return (
      <span className="grid h-3 w-3 place-items-center rounded-full border border-muted-foreground/30 text-muted-foreground/70">
        <X className="h-2 w-2 stroke-[2.4]" aria-hidden />
      </span>
    );
  }
  if (tone === "done") {
    return (
      <span className="grid h-3 w-3 place-items-center rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
        <Check className="h-2 w-2 stroke-[2.4]" aria-hidden />
      </span>
    );
  }
  if (tone === "active") {
    if (running) {
      return (
        <Loader2
          className="h-3 w-3 animate-spin text-[hsl(var(--composer-plan))]"
          strokeWidth={1.8}
          aria-hidden
        />
      );
    }
    return (
      <CircleDot
        className="h-3 w-3 text-[hsl(var(--composer-plan))]"
        strokeWidth={1.8}
        aria-hidden
      />
    );
  }
  if (tone === "blocked") {
    return (
      <CircleDot
        className="h-3 w-3 text-amber-500/80"
        strokeWidth={1.8}
        aria-hidden
      />
    );
  }
  if (tone === "review") {
    return (
      <CircleDot
        className="h-3 w-3 text-[hsl(var(--composer-review))]"
        strokeWidth={1.8}
        aria-hidden
      />
    );
  }
  if (tone === "fix") {
    return (
      <CircleDot
        className="h-3 w-3 text-[hsl(var(--composer-security))]"
        strokeWidth={1.8}
        aria-hidden
      />
    );
  }
  if (tone === "planned") {
    return (
      <CircleDot
        className="h-3 w-3 text-[hsl(var(--composer-ask))]/75"
        strokeWidth={1.8}
        aria-hidden
      />
    );
  }
  return (
    <span
      className={cn(
        "grid h-3 w-3 place-items-center rounded-full border",
        current
          ? "border-[hsl(var(--composer-plan))]/45"
          : "border-muted-foreground/25",
      )}
    />
  );
}
