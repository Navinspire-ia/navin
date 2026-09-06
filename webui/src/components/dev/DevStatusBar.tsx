import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { TFunction } from "i18next";
import {
  AlertTriangle,
  Bot,
  Gauge,
  GitBranch,
  GitCompare,
  Hourglass,
  MonitorSmartphone,
  ShieldCheck,
  XCircle,
  Zap,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useGatewayPace, useGatewayStallSeconds } from "@/hooks/useGatewayPace";
import type { RuntimeHealth } from "@/lib/api";
import type {
  ConnectionStatus,
  ContextUsagePayload,
  FileDiagnosticsPayload,
  GithubCiStatusPayload,
  GitStatusPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

import { ContextUsagePopover } from "./ContextUsagePopover";
import { formatTokenCount } from "./devWorkbenchUtils";

/**
 * One quiet chip while gateway reads are being retried, instead of a red
 * message in every panel that happened to ask during the stall. It names the
 * engine, not the IDE, and disappears on the next answer.
 */
export function GatewayPaceChip() {
  const { t } = useTranslation();
  const pace = useGatewayPace();
  const seconds = useGatewayStallSeconds(pace);
  if (pace.stalledSince === null || pace.stall === null) return null;
  const unreachable = pace.stall === "unreachable";
  return (
    <span
      className={cn(
        "flex shrink-0 items-center gap-1 font-medium",
        unreachable ? "text-red-500" : "text-amber-700 dark:text-amber-400",
      )}
      title={t(unreachable ? "transport.pace.unreachableHint" : "transport.pace.slowHint", {
        seconds,
      })}
      role="status"
      aria-live="polite"
      data-testid="gateway-pace-chip"
      data-stall={pace.stall}
    >
      <Hourglass className="h-3 w-3 animate-pulse" aria-hidden />
      {t(unreachable ? "transport.pace.unreachable" : "transport.pace.slow")}
      <span className="tabular-nums text-muted-foreground">{seconds}s</span>
    </span>
  );
}

function runtimeHealthBarLabel(health: RuntimeHealth, t: TFunction): string {
  if (!health.pressure) {
    return t("dev.status.runtimeOk", { defaultValue: "Host ok" });
  }
  const mem = Math.round((health.memory?.usedRatio ?? 0) * 100);
  const disk = Math.round((health.disk?.usedRatio ?? 0) * 100);
  const reasons = health.reasons ?? [];
  if (reasons.includes("memory") && reasons.includes("disk")) {
    return t("dev.status.runtimePressureBoth", {
      defaultValue: "RAM {{mem}}% · disk {{disk}}%",
      mem,
      disk,
    });
  }
  if (reasons.includes("memory")) {
    return t("dev.status.runtimePressureMemory", {
      defaultValue: "RAM {{pct}}%",
      pct: mem,
    });
  }
  if (reasons.includes("disk")) {
    return t("dev.status.runtimePressureDisk", {
      defaultValue: "Disk {{pct}}%",
      pct: disk,
    });
  }
  return (
    health.label
    || t("dev.status.runtimePressure", {
      defaultValue: "Host {{level}}",
      level: health.level,
    })
  );
}

export function DevStatusBar({
  connection,
  environment,
  git,
  ci,
  projectLabel,
  activeDiagnostics,
  activeFileName,
  runningAgents,
  pendingReviewCount,
  contextUsage,
  lastTabAssist,
  runtimeHealth,
  onPendingReviewClick,
  onProblemsClick,
}: {
  connection: ConnectionStatus;
  environment: string | null;
  git: GitStatusPayload | null;
  ci: GithubCiStatusPayload | null;
  projectLabel: string;
  activeDiagnostics: FileDiagnosticsPayload | null;
  activeFileName: string | null;
  runningAgents: number;
  pendingReviewCount: number;
  contextUsage: ContextUsagePayload | null;
  lastTabAssist: {
    latencyMs: number;
    ttftMs?: number;
    route?: string;
    p50?: number;
    p90?: number;
    sampleCount?: number;
  } | null;
  runtimeHealth: RuntimeHealth | null;
  onPendingReviewClick?: () => void;
  onProblemsClick?: () => void;
}) {
  const { t } = useTranslation();
  const [contextOpen, setContextOpen] = useState(false);
  const contextButtonRef = useRef<HTMLButtonElement>(null);
  const contextPopoverRef = useRef<HTMLDivElement>(null);
  const [contextPos, setContextPos] = useState<{ bottom: number; right: number } | null>(
    null,
  );
  const errors = activeDiagnostics?.errors ?? 0;
  const warnings = activeDiagnostics?.warnings ?? 0;
  const analyzed = activeDiagnostics?.supported === true;
  const contextPercent =
    contextUsage?.percent ?? null;
  const connected = connection === "open";
  const connecting = connection === "connecting" || connection === "reconnecting";
  const stalled = useGatewayPace().stall !== null;

  useEffect(() => {
    if (!contextOpen) return;
    const updatePos = () => {
      const el = contextButtonRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      setContextPos({
        bottom: window.innerHeight - rect.top + 4,
        right: window.innerWidth - rect.right,
      });
    };
    updatePos();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (contextButtonRef.current?.contains(target)) return;
      if (contextPopoverRef.current?.contains(target)) return;
      setContextOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setContextOpen(false);
    };
    window.addEventListener("resize", updatePos);
    window.addEventListener("scroll", updatePos, true);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("resize", updatePos);
      window.removeEventListener("scroll", updatePos, true);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [contextOpen]);

  return (
    <div className="relative z-20 flex h-6 shrink-0 items-center gap-3 overflow-x-auto overflow-y-visible border-t border-border/55 bg-muted/25 px-2.5 text-[11px] text-muted-foreground">
      <span
        className="flex shrink-0 items-center gap-1"
        title={t(`connection.${connection}`)}
        aria-live="polite"
        role="status"
      >
        <span className="relative flex h-2 w-2" aria-hidden>
          {connecting || stalled ? (
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500 opacity-75" />
          ) : null}
          <span
            className={cn(
              "relative inline-flex h-2 w-2 rounded-full",
              // An open socket while HTTP reads time out is still a stalled
              // engine: the dot must not say "all good" next to the chip.
              connected && !stalled
                ? "bg-emerald-500"
                : connecting || stalled
                  ? "bg-amber-500"
                  : "bg-red-500",
            )}
          />
        </span>
        <span className="sr-only">{t(`connection.${connection}`)}</span>
      </span>
      <GatewayPaceChip />
      {environment ? (
        <span className="flex shrink-0 items-center gap-1" title={t("dev.status.environment", { defaultValue: "Environment" })}>
          <MonitorSmartphone className="h-3 w-3" aria-hidden />
          {environment}
        </span>
      ) : null}
      {runtimeHealth ? (
        <span
          className={cn(
            "flex shrink-0 items-center gap-1",
            runtimeHealth.level === "critical" &&
              "font-medium text-red-500",
            runtimeHealth.level === "warning" &&
              "font-medium text-amber-700 dark:text-amber-400",
          )}
          title={
            runtimeHealth.message ||
            t("dev.status.runtimeHealthTooltip", {
              defaultValue:
                "Runtime {{level}} - RAM {{mem}}% used, disk {{disk}}% used",
              level: runtimeHealth.level,
              mem: Math.round((runtimeHealth.memory?.usedRatio ?? 0) * 100),
              disk: Math.round((runtimeHealth.disk?.usedRatio ?? 0) * 100),
            })
          }
        >
          <Gauge className="h-3 w-3" aria-hidden />
          {runtimeHealthBarLabel(runtimeHealth, t)}
        </span>
      ) : null}
      {git?.is_repo ? (
        <span
          className="flex min-w-0 shrink-0 items-center gap-1"
          title={t("dev.status.gitTooltip", {
            defaultValue:
              "Branch {{branch}} - {{staged}} staged, {{unstaged}} modified, {{untracked}} untracked",
            branch: git.branch,
            staged: git.staged ?? 0,
            unstaged: git.unstaged ?? 0,
            untracked: git.untracked ?? 0,
          })}
        >
          <GitBranch className="h-3 w-3" aria-hidden />
          <span className="max-w-[10rem] truncate">
            {git.branch}
            {git.dirty ? "*" : ""}
          </span>
          {git.ahead ? <span>↑{git.ahead}</span> : null}
          {git.behind ? <span>↓{git.behind}</span> : null}
          {(git.staged ?? 0) + (git.unstaged ?? 0) + (git.untracked ?? 0) > 0 ? (
            <span className="font-medium text-foreground/80">
              {(git.staged ?? 0) + (git.unstaged ?? 0) + (git.untracked ?? 0)}{" "}
              {t("dev.status.changes", { defaultValue: "changes" })}
            </span>
          ) : null}
        </span>
      ) : git && git.available === true ? (
        <span className="flex shrink-0 items-center gap-1 opacity-70">
          <GitBranch className="h-3 w-3" aria-hidden />
          {t("dev.status.noRepo", { defaultValue: "no git" })}
        </span>
      ) : null}
      {ci?.available && ci.checks && ci.checks.total > 0 ? (
        ci.pr_url ? (
          <a
            href={ci.pr_url}
            target="_blank"
            rel="noreferrer"
            className={cn(
              "flex shrink-0 items-center gap-1 rounded-sm px-0.5 hover:bg-muted/80",
              ci.checks.state === "failure" && "font-medium text-red-500",
              ci.checks.state === "success" && "text-emerald-600 dark:text-emerald-400",
              ci.checks.state === "pending" && "text-amber-600 dark:text-amber-400",
            )}
            title={t("dev.status.ciTooltip", {
              defaultValue: "CI {{state}} - {{passing}}/{{total}} passing",
              state: ci.checks.state,
              passing: ci.checks.passing,
              total: ci.checks.total,
            })}
          >
            <ShieldCheck className="h-3 w-3" aria-hidden />
            CI {ci.checks.state}
          </a>
        ) : (
          <span
            className={cn(
              "flex shrink-0 items-center gap-1",
              ci.checks.state === "failure" && "font-medium text-red-500",
              ci.checks.state === "success" && "text-emerald-600 dark:text-emerald-400",
              ci.checks.state === "pending" && "text-amber-600 dark:text-amber-400",
            )}
            title={t("dev.status.ciTooltip", {
              defaultValue: "CI {{state}} - {{passing}}/{{total}} passing",
              state: ci.checks.state,
              passing: ci.checks.passing,
              total: ci.checks.total,
            })}
          >
            <ShieldCheck className="h-3 w-3" aria-hidden />
            CI {ci.checks.state}
          </span>
        )
      ) : null}
      <span className="min-w-0 flex-1 truncate">{projectLabel}</span>
      {runningAgents > 0 ? (
        <span
          className="flex shrink-0 items-center gap-1 font-medium text-foreground/80"
          title={t("dev.status.agentsRunningTooltip", {
            defaultValue: "{{count}} agent turn(s) currently executing",
            count: runningAgents,
          })}
        >
          <Bot className="h-3 w-3 animate-pulse" aria-hidden />
          {runningAgents} {t("dev.status.agentsRunning", { defaultValue: "active" })}
        </span>
      ) : null}
      {pendingReviewCount > 0 ? (
        onPendingReviewClick ? (
          <button
            type="button"
            onClick={onPendingReviewClick}
            className="flex shrink-0 items-center gap-1 rounded-sm px-0.5 font-medium text-amber-700 transition-colors hover:bg-amber-500/10 dark:text-amber-400"
            title={t("dev.status.pendingReviewTooltip", {
              defaultValue: "{{count}} agent edit(s) waiting for review - click to open",
              count: pendingReviewCount,
            })}
          >
            <GitCompare className="h-3 w-3" aria-hidden />
            {pendingReviewCount}{" "}
            {t("dev.status.pendingReview", { defaultValue: "to review" })}
          </button>
        ) : (
          <span
            className="flex shrink-0 items-center gap-1"
            title={t("dev.status.pendingReviewTooltip", {
              defaultValue: "{{count}} agent edit(s) waiting for review",
              count: pendingReviewCount,
            })}
          >
            <GitCompare className="h-3 w-3" aria-hidden />
            {pendingReviewCount}{" "}
            {t("dev.status.pendingReview", { defaultValue: "to review" })}
          </span>
        )
      ) : null}
      {lastTabAssist ? (
        <span
          className={cn(
            "flex shrink-0 items-center gap-1 tabular-nums",
            lastTabAssist.latencyMs > 400 && "font-medium text-amber-700 dark:text-amber-400",
          )}
          title={t("dev.status.tabLatencyTooltip", {
            defaultValue:
              "Last Tab completion: {{latency}} ms end-to-end{{ttft}}{{route}}{{pct}}",
            latency: lastTabAssist.latencyMs,
            ttft:
              lastTabAssist.ttftMs !== undefined
                ? `, ${lastTabAssist.ttftMs} ms to first token`
                : "",
            route: lastTabAssist.route ? ` via ${lastTabAssist.route}` : "",
            pct:
              lastTabAssist.p50 !== undefined
                ? ` | local P50 ${lastTabAssist.p50}ms / P90 ${lastTabAssist.p90}ms (n=${lastTabAssist.sampleCount})`
                : "",
          })}
        >
          <Zap className="h-3 w-3" aria-hidden />
          Tab {lastTabAssist.latencyMs}ms
          {lastTabAssist.ttftMs !== undefined ? (
            <span className="opacity-70">·{lastTabAssist.ttftMs}ms</span>
          ) : null}
          {lastTabAssist.p50 !== undefined ? (
            <span className="opacity-70">p50 {lastTabAssist.p50}</span>
          ) : null}
        </span>
      ) : null}
      {contextPercent !== null ? (
        <span className="relative flex shrink-0 items-center">
          <button
            ref={contextButtonRef}
            type="button"
            onClick={() => {
              if (contextOpen) {
                setContextOpen(false);
                return;
              }
              const el = contextButtonRef.current;
              if (el) {
                const rect = el.getBoundingClientRect();
                setContextPos({
                  bottom: window.innerHeight - rect.top + 4,
                  right: window.innerWidth - rect.right,
                });
              }
              setContextOpen(true);
            }}
            className={cn(
              "flex items-center gap-1 rounded-sm px-0.5",
              contextPercent >= 85 && "font-medium text-foreground/80",
              "hover:bg-muted/80",
            )}
            title={t("dev.status.contextTooltip", {
              defaultValue:
                "~{{tokens}} of {{window}} context tokens used ({{source}}){{billed}} - click for why",
              tokens: formatTokenCount(contextUsage?.tokens ?? 0),
              window: formatTokenCount(contextUsage?.context_window ?? 0),
              source: contextUsage?.source ?? "estimate",
              billed:
                contextUsage?.billed_tokens_session
                  ? ` · ${formatTokenCount(contextUsage.billed_tokens_session)} billed this chat`
                  : "",
            })}
            aria-expanded={contextOpen}
            aria-haspopup="dialog"
          >
            <Gauge className="h-3 w-3" aria-hidden />
            {t("dev.status.context", { defaultValue: "context" })} {contextPercent}%
          </button>
          {contextOpen && contextPos && contextUsage
            ? createPortal(
                <ContextUsagePopover
                  usage={contextUsage}
                  pos={contextPos}
                  popoverRef={contextPopoverRef}
                  onClose={() => setContextOpen(false)}
                />,
                document.body,
              )
            : null}
        </span>
      ) : null}
      {activeFileName ? (
        <span className="flex shrink-0 items-center gap-2">
          <span className="max-w-[12rem] truncate">{activeFileName}</span>
          <button
            type="button"
            onClick={onProblemsClick}
            className={cn(
              "flex cursor-pointer items-center gap-0.5 rounded-sm px-0.5",
              errors > 0 && "text-red-500",
              onProblemsClick && "hover:bg-muted/80",
            )}
            title={t("dev.status.errors", { defaultValue: "Errors" })}
          >
            <XCircle className="h-3 w-3" aria-hidden />
            <span className="tabular-nums">{errors}</span>
          </button>
          <button
            type="button"
            onClick={onProblemsClick}
            className={cn(
              "flex cursor-pointer items-center gap-0.5 rounded-sm px-0.5",
              warnings > 0 && "font-medium text-foreground/80",
              onProblemsClick && "hover:bg-muted/80",
            )}
            title={t("dev.status.warnings", { defaultValue: "Warnings" })}
          >
            <AlertTriangle className="h-3 w-3" aria-hidden />
            <span className="tabular-nums">{warnings}</span>
          </button>
          {!analyzed ? (
            <span className="opacity-60">
              {t("dev.status.notAnalyzed", { defaultValue: "no linter" })}
            </span>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}
