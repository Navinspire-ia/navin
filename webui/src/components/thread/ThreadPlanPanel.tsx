// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, CircleDot, Loader2, X } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import {
  Callout, DefaultButton, DirectionalHint, IconButton, MessageBar, MessageBarType,
  PrimaryButton, ProgressIndicator, type IButtonStyles,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import { ACTIVITY_DETAIL_PAGE_SIZE, ActivityPagination } from "./activity/ActivityPagination";

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
  done: "text-muted-foreground",
  active: "font-medium text-foreground",
  planned: "text-foreground/85",
  review: "text-foreground",
  fix: "text-foreground",
  blocked: "text-amber-700 dark:text-amber-400/90",
  cancelled: "text-muted-foreground",
  pending: "text-foreground/85",
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

const COMMAND_STYLES: IButtonStyles = {
  root: { minWidth: 0, height: 40, padding: "0 12px", border: "none", borderRadius: 8,
    background: "transparent", color: "hsl(var(--foreground))", fontSize: 12 },
  rootHovered: { background: "hsl(var(--muted))", color: "hsl(var(--foreground))" },
  rootPressed: { background: "hsl(var(--muted))", color: "hsl(var(--foreground))" },
  rootDisabled: { background: "transparent", color: "hsl(var(--muted-foreground))", opacity: 0.5 },
  icon: { fontSize: 14 },
};
const PRIMARY_STYLES: IButtonStyles = {
  root: { height: 40, minWidth: 80, borderRadius: 8, border: "none",
    background: "hsl(var(--foreground))", color: "hsl(var(--background))", fontSize: 12 },
  rootHovered: { background: "hsl(var(--foreground))", color: "hsl(var(--background))", opacity: 0.9 },
  rootPressed: { background: "hsl(var(--foreground))", color: "hsl(var(--background))" },
  rootDisabled: { background: "hsl(var(--muted))", color: "hsl(var(--muted-foreground))" },
};

/** On-demand plan in the conversation header. Never follows the composer. */
export function ThreadPlanPanel({
  sessionKey, activity = null, isStreaming = false, onBuild, onStop,
}: {
  sessionKey: string | null;
  activity?: string | null;
  isStreaming?: boolean;
  onBuild?: (plan: SessionPlan) => void;
  onStop?: () => void;
}) {
  const { client, token } = useClient();
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const tx = useCallback((key: string, fallback: string) => t(key, { defaultValue: fallback }), [t]);
  const [plan, setPlan] = useState<SessionPlan | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [requestedPage, setPage] = useState(0);
  const [gapsAcknowledged, setGapsAcknowledged] = useState(false);
  const [missionBusy, setMissionBusy] = useState(false);
  const [actionError, setActionError] = useState(false);
  const target = useRef<HTMLDivElement>(null);
  const requestId = useRef(0);
  const panelId = useId();
  const headingId = `${panelId}-title`;
  const gapsCardId = `${panelId}-gaps`;

  const load = useCallback(async () => {
    if (!token || !sessionKey) return;
    const request = ++requestId.current;
    try {
      const board = await fetchBoard(token, sessionKey);
      if (request === requestId.current) setPlan(board.session_plan ?? null);
    } catch {
      // Keep the last known plan when a background refresh fails.
    }
  }, [token, sessionKey]);

  useEffect(() => {
    setPlan(null);
    setExpanded(false);
    setPage(0);
    setActionError(false);
    void load();
    return () => { requestId.current += 1; };
  }, [load]);

  useEffect(() => {
    const unsubscribe = client.onBoardUpdate(() => void load());
    const interval = window.setInterval(() => void load(), POLL_MS);
    return () => { unsubscribe(); window.clearInterval(interval); };
  }, [client, load]);

  const qualitySignature = plan?.quality
    ? `${plan.version ?? 0}|${JSON.stringify(plan.quality)}` : "";
  useEffect(() => { setGapsAcknowledged(false); }, [qualitySignature]);

  const currentItem = useMemo(() => {
    if (!plan || plan.complete) return null;
    return plan.items.find((item) => item.id === plan.current_id && !item.cancelled) ?? null;
  }, [plan]);

  const applyMissionAction = useCallback(async (action: "pause_mission" | "resume_mission") => {
    if (!token || !sessionKey || missionBusy) return;
    setMissionBusy(true);
    setActionError(false);
    try {
      await updateBoard(token, sessionKey, action === "pause_mission"
        ? { action, reason: "human" } : { action });
      await load();
    } catch {
      setActionError(true);
    } finally {
      setMissionBusy(false);
    }
  }, [load, missionBusy, sessionKey, token]);

  if (!plan || plan.items.length === 0) return null;
  const hasRemaining = plan.items.some((item) => !item.done);
  const started = plan.items.some((item) => item.done || item.active || item.cancelled);
  const canBuild = Boolean(onBuild) && !isStreaming && !plan.complete && hasRemaining;
  const canStop = Boolean(onStop) && isStreaming;
  const { canPause, canResume } = missionControlState(plan, isStreaming);
  const buildHeldByGaps = buildHeldByPlanGaps(plan.quality, canBuild, gapsAcknowledged);
  const buildHeldTitle = tx("thread.plan.qualityHoldHint", "Review the missing requirements before starting.");
  const buildLabel = started ? tx("thread.plan.resume", "Resume") : tx("thread.plan.build", "Build");
  const page = Math.min(requestedPage, Math.ceil(plan.items.length / ACTIVITY_DETAIL_PAGE_SIZE) - 1);
  const visibleItems = plan.items.slice(page * ACTIVITY_DETAIL_PAGE_SIZE, (page + 1) * ACTIVITY_DETAIL_PAGE_SIZE);
  const title = plan.goal || plan.title || tx("thread.plan.created", "Plan");
  const status = plan.complete ? tx("thread.plan.complete", "Plan complete")
    : plan.ledger_status === "paused" ? tx("thread.planPaused", "Paused")
    : isStreaming ? tx("thread.plan.inProgress", "In progress")
    : tx("thread.plan.readyToResume", "Ready to continue");

  return (
    <div ref={target} className="host-no-drag shrink-0" data-testid="thread-plan-panel">
      <DefaultButton
        data-testid="thread-plan-toggle"
        text={`${tx("thread.plan.created", "Plan")} ${plan.done_count}/${plan.total_count}`}
        iconProps={{ iconName: plan.complete ? "CheckMark" : "BulletedList" }}
        title={title}
        aria-expanded={expanded}
        aria-controls={expanded ? panelId : undefined}
        aria-haspopup="dialog"
        onClick={() => setExpanded((open) => !open)}
        styles={COMMAND_STYLES}
        className="tabular-nums"
      />
      {expanded ? (
        <Callout
          target={target.current}
          role="dialog"
          ariaLabelledBy={headingId}
          directionalHint={DirectionalHint.bottomRightEdge}
          gapSpace={8}
          isBeakVisible={false}
          setInitialFocus
          onDismiss={() => setExpanded(false)}
          styles={{
            root: { width: "min(400px, calc(100vw - 24px))", borderRadius: 12,
              // Animate the contents only; keep the surface opaque from the first frame.
              selectors: { "&&": { animation: "none" } },
              boxShadow: "0 12px 36px rgba(0,0,0,0.18), 0 0 0 1px hsl(var(--border))" },
            calloutMain: { borderRadius: 12, background: "hsl(var(--background))", color: "hsl(var(--foreground))" },
          }}
        >
          <motion.section
            id={panelId}
            data-testid="thread-plan-details"
            initial={reduceMotion ? false : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", duration: 0.3, bounce: 0 }}
            className="max-h-[calc(100dvh-100px)] overflow-y-auto p-4"
          >
            <div className="flex items-center justify-between gap-3">
              <h2 id={headingId} className="text-sm font-semibold">{tx("thread.plan.created", "Plan")}</h2>
              <IconButton
                data-testid="thread-plan-hide"
                iconProps={{ iconName: "Cancel" }}
                ariaLabel={tx("thread.plan.close", "Close plan")}
                onClick={() => setExpanded(false)}
                styles={COMMAND_STYLES}
              />
            </div>
            <p className="mt-1 text-sm font-medium leading-relaxed [text-wrap:pretty]">{title}</p>
            <div className="mb-2 mt-3 flex items-center justify-between gap-3 text-xs text-muted-foreground">
              <span>{status}</span>
              <span className="tabular-nums">{plan.done_count} / {plan.total_count}</span>
            </div>
            <ProgressIndicator
              ariaLabel={t("thread.plan.progress", { defaultValue: "{{done}} of {{total}} steps completed", done: plan.done_count, total: plan.total_count })}
              percentComplete={plan.total_count ? Math.min(1, plan.done_count / plan.total_count) : 0}
              barHeight={3}
              styles={{ itemProgress: { padding: 0 }, progressTrack: { background: "hsl(var(--muted))" }, progressBar: { background: "hsl(var(--foreground))" } }}
            />
            {currentItem ? <p className="mt-3 text-xs text-muted-foreground">{tx("thread.plan.now", "Now")}: <span className="text-foreground">{currentItem.title}</span></p> : null}
            <ActivityPagination page={page} total={plan.items.length} onPageChange={setPage} />
            <ul className="my-3 max-h-80 space-y-1 overflow-y-auto" aria-label={tx("thread.plan.steps", "Steps")}>
              {visibleItems.map((item) => (
                <PlanRow key={item.id} item={item}
                  current={item.id === plan.current_id && !plan.complete && !item.cancelled}
                  running={isStreaming && !reduceMotion}
                  activity={item.id === plan.current_id && isStreaming ? activity : null}
                  cancelledLabel={tx("thread.plan.cancelled", "Cancelled")}
                />
              ))}
            </ul>
            {plan.loop_detected || (plan.stall_count ?? 0) > 0 ? (
              <MessageBar messageBarType={MessageBarType.warning}>
                {tx("thread.plan.needsProgress", "Progress has stalled. The agent needs to change approach.")}
              </MessageBar>
            ) : null}
            {planQualityVerdict(plan.quality, canBuild) === "gaps" && plan.quality ? (
              <div id={gapsCardId} data-testid="thread-plan-quality-gaps" className="mb-3">
                <MessageBar messageBarType={MessageBarType.warning} isMultiline>
                  {plan.quality.gaps.slice(0, 3).map((gap, index) => <p key={index}>{gap.message}</p>)}
                  {buildHeldByGaps ? (
                    <DefaultButton data-testid="thread-plan-build-anyway"
                      onClick={() => { setGapsAcknowledged(true); onBuild?.(plan); setExpanded(false); }}
                      className="mt-2"
                      text={started ? tx("thread.plan.qualityResumeAnyway", "Resume anyway") : tx("thread.plan.qualityBuildAnyway", "Build anyway")}
                      styles={COMMAND_STYLES}
                    />
                  ) : null}
                </MessageBar>
              </div>
            ) : null}
            {actionError ? <MessageBar messageBarType={MessageBarType.error}>{tx("thread.plan.actionFailed", "The plan could not be updated. Please try again.")}</MessageBar> : null}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3">
              <DefaultButton data-testid="thread-plan-view"
                onClick={() => { preloadMarkdownText(); requestOpenSessionPlan(); setExpanded(false); }}
                className="shrink-0"
                text={tx("thread.plan.view", "View plan")}
                styles={COMMAND_STYLES}
              />
              <div className="flex items-center gap-1">
                {canPause || canResume ? (
                  <IconButton
                    data-testid={canResume ? "thread-plan-resume-mission" : "thread-plan-pause-mission"}
                    iconProps={{ iconName: canResume ? "Play" : "Pause" }}
                    ariaLabel={canResume ? tx("thread.plan.resumeMission", "Resume mission") : tx("thread.plan.pauseMission", "Pause mission")}
                    title={canResume ? tx("thread.plan.resumeMission", "Resume mission") : tx("thread.plan.pauseMission", "Pause mission")}
                    disabled={missionBusy}
                    onClick={() => void applyMissionAction(canResume ? "resume_mission" : "pause_mission")}
                    styles={COMMAND_STYLES}
                  />
                ) : null}
                {canStop ? <PrimaryButton data-testid="thread-plan-stop" text={tx("thread.plan.stop", "Stop")} onClick={onStop} styles={PRIMARY_STYLES} /> : null}
                {canBuild ? (
                  <PrimaryButton data-testid="thread-plan-build" text={buildLabel}
                    onClick={() => { onBuild?.(plan); setExpanded(false); }}
                    disabled={buildHeldByGaps}
                    title={buildHeldByGaps ? buildHeldTitle : undefined}
                    aria-describedby={buildHeldByGaps ? gapsCardId : undefined}
                    styles={PRIMARY_STYLES}
                  />
                ) : null}
              </div>
            </div>
          </motion.section>
        </Callout>
      ) : null}
    </div>
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
        "flex min-w-0 items-start gap-2 rounded-lg px-2 py-2 text-[13px] leading-normal",
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
              className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[hsl(var(--composer-plan))] motion-reduce:animate-none"
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
