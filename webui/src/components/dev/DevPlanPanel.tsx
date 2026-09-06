import { Hammer, Loader2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { MarkdownText, preloadMarkdownText } from "@/components/MarkdownText";
import {
  fetchBoard,
  type BoardMission,
  type BoardTask,
  type SessionPlan,
} from "@/lib/api";
import { formatSessionPlanMarkdown } from "@/lib/plan-markdown";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

/** Same cadence as the chat plan card: a missed board_updated still heals. */
const POLL_MS = 20_000;

/**
 * Full session plan in the Code column.
 *
 * "View plan" used to open a right-hand Sheet over the chat. That read as a
 * second window. This panel is a workbench tab, like Tasks or Git.
 */
export function DevPlanPanel({
  sessionKey,
  onRunAction,
  onClose,
}: {
  sessionKey: string | null;
  onRunAction?: (text: string) => void;
  onClose?: () => void;
}) {
  const { client, token } = useClient();
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [plan, setPlan] = useState<SessionPlan | null>(null);
  const [tasks, setTasks] = useState<BoardTask[]>([]);
  const [mission, setMission] = useState<BoardMission | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token || !sessionKey) {
      setPlan(null);
      setTasks([]);
      setMission(null);
      return;
    }
    setLoading(true);
    try {
      const board = await fetchBoard(token, sessionKey);
      setPlan(board.session_plan ?? null);
      setTasks(board.tasks ?? []);
      setMission(board.mission ?? null);
    } catch {
      setPlan(null);
    } finally {
      setLoading(false);
    }
  }, [sessionKey, token]);

  useEffect(() => {
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

  useEffect(() => {
    preloadMarkdownText();
  }, []);

  const planMarkdown = useMemo(() => {
    if (!plan) return "";
    return formatSessionPlanMarkdown(plan, tasks, mission, {
      overview: tx("thread.plan.md.overview", "Overview"),
      steps: tx("thread.plan.md.steps", "Plan"),
      acceptance: tx("thread.plan.md.acceptance", "Acceptance criteria"),
      constraints: tx("thread.plan.md.constraints", "Constraints"),
      facts: tx("thread.plan.md.facts", "Facts"),
      missingInfo: tx("thread.plan.md.missingInfo", "Missing information"),
      validation: tx("thread.plan.md.validation", "Validation"),
      status: tx("thread.plan.md.status", "Status"),
      done: tx("thread.plan.md.done", "Done"),
      active: tx("thread.plan.md.active", "In progress"),
      blocked: tx("thread.plan.md.blocked", "Blocked"),
      cancelled: tx("thread.plan.cancelled", "Cancelled"),
      pending: tx("thread.plan.md.pending", "Pending"),
    });
  }, [mission, plan, tasks, tx]);

  const title =
    plan?.goal || plan?.title || tx("thread.plan.created", "Plan");
  const hasRemaining = Boolean(plan?.items.some((item) => !item.done));
  const started = Boolean(
    plan?.items.some((item) => item.done || item.active || item.cancelled),
  );
  const canBuild =
    Boolean(onRunAction) && Boolean(plan) && !plan?.complete && hasRemaining;

  const onBuild = () => {
    if (!plan || !onRunAction) return;
    const pending = plan.items
      .filter((item) => !item.done)
      .map((item) => `- ${item.title}`)
      .join("\n");
    onRunAction(
      pending
        ? `/forge Implement the approved plan.\n\n${pending}`
        : "/forge Implement the approved plan.",
    );
  };

  if (!sessionKey) {
    return (
      <div
        className="flex min-h-0 flex-1 items-center justify-center px-6 text-center text-[13px] text-muted-foreground"
        data-testid="dev-plan-panel"
      >
        {tx("dev.planEmpty", "No session plan yet. Ask the agent to plan, then open it here.")}
      </div>
    );
  }

  if (loading && !plan) {
    return (
      <div
        className="flex min-h-0 flex-1 items-center justify-center text-muted-foreground"
        data-testid="dev-plan-panel"
      >
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      </div>
    );
  }

  if (!plan || plan.items.length === 0) {
    return (
      <div
        className="flex min-h-0 flex-1 items-center justify-center px-6 text-center text-[13px] text-muted-foreground"
        data-testid="dev-plan-panel"
      >
        {tx("dev.planEmpty", "No session plan yet. Ask the agent to plan, then open it here.")}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background" data-testid="dev-plan-panel">
      <div className="flex shrink-0 items-start justify-between gap-3 px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
          <p className="mt-1 text-[12px] text-muted-foreground">
            {tx(
              "thread.plan.viewHint",
              "Full plan in Markdown - steps, acceptance, and context.",
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
        {canBuild ? (
          <button
            type="button"
            data-testid="dev-plan-build"
            onClick={onBuild}
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1",
              "bg-foreground text-[12px] font-semibold text-background",
              "transition-transform active:scale-[0.96] hover:bg-foreground/90",
            )}
          >
            <Hammer className="h-3 w-3" aria-hidden />
            {started
              ? tx("thread.plan.resume", "Resume")
              : tx("thread.plan.build", "Build")}
          </button>
        ) : null}
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
            aria-label={tx("dev.planClose", "Close plan")}
            title={tx("dev.planClose", "Close plan")}
            data-testid="dev-plan-close"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div
          className={cn(
            "prose prose-sm max-w-none dark:prose-invert",
            "prose-headings:font-semibold prose-headings:tracking-tight",
            "prose-h1:mb-2 prose-h1:text-[1.35rem]",
            "prose-h2:mt-6 prose-h2:mb-2 prose-h2:text-[1.05rem]",
            "prose-h3:mt-5 prose-h3:mb-1.5 prose-h3:text-[0.95rem]",
            "prose-p:my-2 prose-p:leading-relaxed",
            "prose-li:my-0.5 prose-ul:my-2",
            "prose-strong:text-foreground",
          )}
        >
          <MarkdownText>{planMarkdown}</MarkdownText>
        </div>
      </div>
    </div>
  );
}

export default DevPlanPanel;
