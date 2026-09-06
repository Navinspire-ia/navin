import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  BrainCircuit,
  GitBranch,
  GitPullRequest,
  Github,
  Loader2,
  Lock,
  RefreshCw,
  Repeat,
  ShieldCheck,
  Wrench,
  Zap,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { ToggleButton } from "@/components/settings/ToggleButton";
import { Button } from "@/components/ui/button";
import {
  fetchBoard,
  fetchGitStatus,
  fetchSettings,
  updateBoardAutonomy,
  updateSettings,
  type BoardAutonomy,
} from "@/lib/api";
import type { SettingsPayload } from "@/lib/types";
import { bindMenuListWheel } from "@/lib/model-picker-scroll";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

import { DevGuardrailsSecurity } from "./DevGuardrailsSecurity";

/** Machine-wide kill-switches; they can only subtract from project consent. */
type GlobalFlags = { auto_branch_enabled: boolean; open_pr_enabled: boolean };

type ToggleKey =
  | "enabled"
  | "auto_branch"
  | "open_pr_on_done"
  | "sync_github_issues"
  | "fix_issues"
  | "autopilot_loop";

/** True only when project consent is on and the machine kill-switch is off. */
export function projectPermissionBlocked(
  projectEnabled: boolean,
  machineAllowed?: boolean,
): boolean {
  return projectEnabled && machineAllowed === false;
}

/** Hash of Settings > Security for the current chat, restart button included. */
export function securitySettingsHash(sessionKey: string | null): string {
  const params = new URLSearchParams();
  if (sessionKey) params.set("chat", sessionKey);
  params.set("section", "advanced");
  return `#/settings?${params.toString()}`;
}

function globalFlagsFrom(settings: SettingsPayload | null): GlobalFlags {
  return {
    auto_branch_enabled: settings?.board_git?.auto_branch_enabled ?? true,
    open_pr_enabled: settings?.board_git?.open_pr_enabled ?? true,
  };
}

export type PillTone = "on" | "off" | "blocked";

export const PILL_TONE: Record<PillTone, string> = {
  on: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  blocked: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  off: "bg-muted text-muted-foreground",
};

export const ICON_TONE: Record<PillTone, string> = {
  on: "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400",
  blocked: "bg-amber-500/12 text-amber-600 dark:text-amber-400",
  off: "bg-muted/80 text-muted-foreground",
};

/** Small uppercase status pill (On / Off / Blocked). Shared with the AGI panel. */
export function StatusPill({ tone, children }: { tone: PillTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-full px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide",
        PILL_TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

/** One switch row: icon, title, status pill, detail, toggle. Shared with the AGI panel. */
export function GuardrailRow({
  checked,
  machineAllowed,
  disabled,
  onChange,
  icon: Icon,
  title,
  detail,
  blockedNote,
  testId,
  emphasis = false,
}: {
  checked: boolean;
  /**
   * Machine kill-switch for this permission. ``false`` means the project
   * consent cannot apply. ``undefined`` means there is no machine gate.
   * Do not derive Blocked from ``effective``: that flag is also false when
   * Autonomy is off, which is already explained above the list.
   */
  machineAllowed?: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
  icon: typeof Zap;
  title: string;
  detail: string;
  blockedNote?: string;
  testId: string;
  /** The master switch of a card: a tinted band when it is on. */
  emphasis?: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const blocked = projectPermissionBlocked(checked, machineAllowed);
  const tone: PillTone = checked ? (blocked ? "blocked" : "on") : "off";

  return (
    <div
      className={cn(
        "flex items-start gap-3 px-4 py-3 transition-colors",
        emphasis && checked && "bg-emerald-500/[0.06] dark:bg-emerald-500/[0.08]",
        disabled && "opacity-60",
      )}
      data-testid={testId}
      data-state={tone}
    >
      <span
        className={cn(
          "mt-px flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-colors",
          ICON_TONE[tone],
        )}
        aria-hidden
      >
        <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium leading-5 text-foreground">{title}</span>
          <StatusPill tone={tone}>
            {checked
              ? blocked
                ? tx("dev.guardrails.blocked", "Blocked")
                : tx("dev.guardrails.on", "On")
              : tx("dev.guardrails.off", "Off")}
          </StatusPill>
        </div>
        <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">{detail}</p>
        <AnimatePresence initial={false}>
          {blocked && blockedNote ? (
            <motion.p
              key="blocked"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.18 }}
              className="overflow-hidden text-[11.5px] leading-5 text-amber-700 dark:text-amber-300"
            >
              <span className="inline-flex items-center gap-1 pt-1">
                <Lock className="h-3 w-3 shrink-0" aria-hidden />
                {blockedNote}
              </span>
            </motion.p>
          ) : null}
        </AnimatePresence>
      </div>
      <div className="pt-0.5">
        <ToggleButton
          checked={checked}
          disabled={disabled}
          onChange={onChange}
          ariaLabel={title}
          label={checked ? tx("dev.guardrails.on", "On") : tx("dev.guardrails.off", "Off")}
        />
      </div>
    </div>
  );
}

/** Uppercase section title with an optional hint and action. Shared with the AGI panel. */
export function Section({
  title,
  hint,
  action,
  index,
  children,
  testId,
}: {
  title: string;
  hint?: string | null;
  action?: ReactNode;
  index: number;
  children: ReactNode;
  testId?: string;
}) {
  const reduced = useReducedMotion();
  return (
    <motion.section
      initial={reduced ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, delay: index * 0.04, ease: [0.2, 0.8, 0.2, 1] }}
      className="space-y-2"
      data-testid={testId}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-1">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {title}
        </h3>
        {hint ? (
          <p className="min-w-0 flex-1 text-[11.5px] leading-5 text-muted-foreground/80">{hint}</p>
        ) : null}
        {action ? <div className="ml-auto shrink-0">{action}</div> : null}
      </div>
      {children}
    </motion.section>
  );
}

/** Rounded card whose children are separated by hairlines. Shared with the AGI panel. */
export function Card({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <div
      className="overflow-hidden rounded-2xl border border-border/55 bg-card/40 divide-y divide-border/40"
      data-testid={testId}
    >
      {children}
    </div>
  );
}

/**
 * One place that answers "what is the agent allowed to do, right now": to
 * the repo (autonomy, branches, pull requests), on this machine (the
 * kill-switches) and around it (the Security switches of Settings). The
 * same switches existed before, split between the Tasks panel and
 * Settings, which meant a branch switch could happen without the user
 * ever having seen the toggle that caused it.
 *
 * What the agent may *learn* (episodic memory, skills evolution) lives in
 * the AGI panel next door; a link at the bottom points there.
 */
export function DevGuardrailsPanel({
  sessionKey,
  projectPath,
  onRunAction,
  onOpenAgi,
}: {
  sessionKey: string | null;
  projectPath?: string | null;
  /** Seeds a chat turn: the autopilot loop is a cron the agent owns. */
  onRunAction?: (text: string) => void;
  /** Opens the AGI panel (memory and skills evolution moved there). */
  onOpenAgi?: () => void;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const boardKey = sessionKey ?? "websocket:webui-dev";
  const [autonomy, setAutonomy] = useState<BoardAutonomy | null>(null);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [branch, setBranch] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const flags = globalFlagsFrom(settings);

  useLayoutEffect(() => {
    const node = scrollRef.current;
    return node ? bindMenuListWheel(node) : undefined;
  });

  const loadBranch = useCallback(async () => {
    if (!token || !projectPath) return;
    try {
      const git = await fetchGitStatus(token, projectPath);
      setBranch(git.is_repo ? (git.branch ?? null) : null);
    } catch {
      setBranch(null);
    }
  }, [token, projectPath]);

  const load = useCallback(
    async (options?: { silent?: boolean }) => {
      if (!token) return;
      if (!options?.silent) setLoading(true);
      try {
        const [board, payload] = await Promise.all([
          fetchBoard(token, boardKey),
          fetchSettings(token),
        ]);
        setAutonomy(board.autonomy ?? null);
        setSettings(payload);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
      void loadBranch();
    },
    [token, boardKey, loadBranch],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const saveProject = useCallback(
    async (key: ToggleKey, value: boolean) => {
      if (!token) return;
      setSaving(true);
      // Only ever writes the switch that was touched. Granting autonomy from a
      // sub-permission would hand out task chaining without the consent the
      // Tasks dialog records, so the sub-rows are disabled until it is on.
      const fields = { [key]: value };
      setAutonomy((prev) => (prev ? { ...prev, ...fields } : prev));
      try {
        // The answer is the whole state, effective permissions included.
        const next = await updateBoardAutonomy(token, boardKey, fields);
        setAutonomy(next);
        setError(null);
        // The loop is a session-bound cron the agent owns: the flag alone
        // would be a switch that lies, so ask it to create or remove the job.
        // Only after a successful write: a failed save must not spawn a loop.
        if (key === "autopilot_loop" && onRunAction) {
          onRunAction(
            value
              ? "/board loop"
              : tx(
                  "dev.guardrails.stopLoopPrompt",
                  "Stop the board autopilot: remove the session-bound board loop cron job you created for this project, then confirm.",
                ),
          );
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setSaving(false);
      }
    },
    [token, boardKey, load, onRunAction, tx],
  );

  const saveGlobal = useCallback(
    async (update: { boardAutoBranch?: boolean; boardOpenPr?: boolean }) => {
      if (!token) return;
      setSaving(true);
      try {
        const payload = await updateSettings(token, update);
        setSettings(payload);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSaving(false);
        // Effective permissions depend on the machine switches: refresh
        // them without flashing the whole panel back to loading.
        void load({ silent: true });
      }
    },
    [token, load],
  );

  const openSecuritySettings = useCallback(() => {
    window.location.hash = securitySettingsHash(sessionKey);
  }, [sessionKey]);

  const enabled = autonomy?.enabled ?? false;
  const willBranch = autonomy?.effective?.auto_branch ?? false;
  const blockedNote = tx(
    "dev.guardrails.blockedByMachine",
    "Enabled here but blocked by the machine-wide switch below.",
  );

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden" data-testid="dev-guardrails-panel">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border/60 px-3">
        <span
          className={cn(
            "flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition-colors",
            enabled ? ICON_TONE.on : "bg-muted/80 text-muted-foreground",
          )}
          aria-hidden
        >
          <ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.75} />
        </span>
        <span className="text-[13px] font-semibold">{tx("dev.guardrailsTab", "Guardrails")}</span>
        {autonomy ? (
          <StatusPill tone={enabled ? "on" : "off"}>
            {enabled
              ? tx("dev.guardrails.autonomyOn", "Autonomy on")
              : tx("dev.guardrails.autonomyOff", "Autonomy off")}
          </StatusPill>
        ) : null}
        {saving ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden />
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ml-auto h-7 gap-1.5 px-2 text-[12px]"
          onClick={() => void load()}
          disabled={loading}
          data-testid="dev-guardrails-refresh"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
          {tx("dev.guardrails.refresh", "Refresh")}
        </Button>
      </div>

      <AnimatePresence initial={false}>
        {error ? (
          <motion.div
            key="error"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="shrink-0 overflow-hidden border-b border-border/60 bg-destructive/10"
          >
            <div className="flex items-center gap-2 px-3 py-2 text-[12px] text-destructive">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="truncate">{error}</span>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div
        ref={scrollRef}
        data-panel-scroll=""
        data-testid="dev-guardrails-scroll"
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
      >
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-4">
          <motion.div
            initial={false}
            animate={{ opacity: 1 }}
            className={cn(
              "flex items-start gap-3 rounded-2xl border px-4 py-3",
              willBranch
                ? "border-amber-500/30 bg-amber-500/10"
                : "border-border/55 bg-card/40",
            )}
            data-testid="dev-guardrails-branch-banner"
          >
            <span
              className={cn(
                "mt-px flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
                willBranch ? ICON_TONE.blocked : "bg-muted/80 text-muted-foreground",
              )}
              aria-hidden
            >
              <GitBranch className="h-3.5 w-3.5" strokeWidth={1.75} />
            </span>
            <div className="min-w-0">
              <p className="text-[13px] font-medium leading-5">
                {branch
                  ? t("dev.guardrails.onBranch", {
                      defaultValue: "You are on {{branch}}",
                      branch,
                    })
                  : tx("dev.guardrails.noRepo", "No git branch detected")}
              </p>
              <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
                {willBranch
                  ? tx(
                      "dev.guardrails.willBranch",
                      "Claiming a task moves you to navin/task-<id> before any write. Turn off Auto-branch below to keep committing here.",
                    )
                  : tx(
                      "dev.guardrails.willStay",
                      "The agent commits on this branch. It will not create or switch branches on its own.",
                    )}
              </p>
            </div>
          </motion.div>

          <Section
            index={0}
            title={tx("dev.guardrails.projectSection", "This project")}
            hint={
              enabled
                ? null
                : tx(
                    "dev.guardrails.autonomyOffNote",
                    "Autonomy is off, so the permissions below have no effect. Turn it on to choose them.",
                  )
            }
            testId="dev-guardrails-project"
          >
            <Card>
              <GuardrailRow
                testId="dev-guardrails-enabled"
                emphasis
                checked={enabled}
                onChange={(next) => void saveProject("enabled", next)}
                disabled={saving || !autonomy}
                icon={Zap}
                title={tx("dev.guardrails.autonomy", "Autonomy")}
                detail={tx(
                  "dev.guardrails.autonomyDetail",
                  "The agent chains through ready board tasks without asking again per task. Off: it only acts inside a run you start.",
                )}
              />
              <GuardrailRow
                testId="dev-guardrails-autoBranch"
                checked={autonomy?.auto_branch ?? false}
                machineAllowed={flags.auto_branch_enabled}
                onChange={(next) => void saveProject("auto_branch", next)}
                disabled={saving || !enabled}
                icon={GitBranch}
                title={tx("dev.guardrails.autoBranch", "Auto-branch per task")}
                detail={tx(
                  "dev.guardrails.autoBranchDetail",
                  "Create and switch to navin/task-<id> when a task is claimed. Off (default): the agent writes on your current branch.",
                )}
                blockedNote={blockedNote}
              />
              <GuardrailRow
                testId="dev-guardrails-openPr"
                checked={autonomy?.open_pr_on_done ?? false}
                machineAllowed={flags.open_pr_enabled}
                onChange={(next) => void saveProject("open_pr_on_done", next)}
                disabled={saving || !enabled}
                icon={GitPullRequest}
                title={tx("dev.guardrails.openPr", "Pull request on done")}
                detail={tx(
                  "dev.guardrails.openPrDetail",
                  "Push the task branch and open a pull request when a task reaches done. Skipped when the task has no branch of its own.",
                )}
                blockedNote={blockedNote}
              />
              <GuardrailRow
                testId="dev-guardrails-syncIssues"
                checked={autonomy?.sync_github_issues ?? false}
                onChange={(next) => void saveProject("sync_github_issues", next)}
                disabled={saving || !enabled}
                icon={Github}
                title={tx("dev.guardrails.syncIssues", "Mirror forge issues")}
                detail={tx(
                  "dev.guardrails.syncIssuesDetail",
                  "Create issues mirroring board tasks and close them when the task is done.",
                )}
              />
              <GuardrailRow
                testId="dev-guardrails-fixIssues"
                checked={autonomy?.fix_issues ?? false}
                onChange={(next) => void saveProject("fix_issues", next)}
                disabled={saving || !enabled}
                icon={Wrench}
                title={tx("dev.guardrails.fixIssues", "Fix issues end to end")}
                detail={tx(
                  "dev.guardrails.fixIssuesDetail",
                  "Reproduce, fix, run the tests until green, commit, then close the issue and re-sync the board.",
                )}
              />
              <GuardrailRow
                testId="dev-guardrails-loop"
                checked={autonomy?.autopilot_loop ?? false}
                onChange={(next) => void saveProject("autopilot_loop", next)}
                disabled={saving || !enabled}
                icon={Repeat}
                title={tx("dev.guardrails.loop", "Autopilot loop")}
                detail={tx(
                  "dev.guardrails.loopDetail",
                  "A session-bound cron job processes the board continuously, one ready task per cycle, even with no chat open.",
                )}
              />
            </Card>
          </Section>

          <Section
            index={1}
            title={tx("dev.guardrails.machineSectionTitle", "This machine")}
            hint={tx(
              "dev.guardrails.machineSectionHint",
              "Kill-switches: off here wins over every project, whatever its own setting says.",
            )}
            testId="dev-guardrails-machine"
          >
            <Card>
              <GuardrailRow
                testId="dev-guardrails-machineAutoBranch"
                checked={flags.auto_branch_enabled}
                onChange={(next) => void saveGlobal({ boardAutoBranch: next })}
                disabled={saving || !settings}
                icon={GitBranch}
                title={tx("dev.guardrails.machineAutoBranch", "Allow task auto-branch")}
                detail={tx(
                  "dev.guardrails.machineAutoBranchDetail",
                  "Off: no project on this machine may create a task branch, whatever its own setting says.",
                )}
              />
              <GuardrailRow
                testId="dev-guardrails-machineOpenPr"
                checked={flags.open_pr_enabled}
                onChange={(next) => void saveGlobal({ boardOpenPr: next })}
                disabled={saving || !settings}
                icon={GitPullRequest}
                title={tx("dev.guardrails.machineOpenPr", "Allow pull request on done")}
                detail={tx(
                  "dev.guardrails.machineOpenPrDetail",
                  "Off: no project on this machine may push a branch and open a pull request on its own.",
                )}
              />
            </Card>
          </Section>

          <Section
            index={2}
            title={tx("dev.guardrails.securitySection", "Security")}
            hint={tx(
              "dev.guardrails.securityHint",
              "The same switches as Settings > Security, saved to the same place.",
            )}
            testId="dev-guardrails-security-section"
          >
            <DevGuardrailsSecurity
              settings={settings}
              onSettingsChange={setSettings}
              onOpenSettings={openSecuritySettings}
            />
          </Section>

          <div
            className="flex items-start gap-3 rounded-2xl border border-border/55 bg-card/40 px-4 py-3"
            data-testid="dev-guardrails-agi-link"
          >
            <span
              className={cn(
                "mt-px flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
                ICON_TONE.off,
              )}
              aria-hidden
            >
              <BrainCircuit className="h-3.5 w-3.5" strokeWidth={1.75} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-medium leading-5">
                {tx("dev.guardrails.agiMoved", "Memory and skills evolution live in AGI")}
              </p>
              <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
                {tx(
                  "dev.guardrails.agiMovedDetail",
                  "Episodic memory, the recall tool and the skill drafts Navin writes on its own are switched in the AGI panel, first in the rail after the project folder.",
                )}
              </p>
              <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground" data-testid="dev-guardrails-transfer-note">
                {tx(
                  "dev.guardrails.transferNote",
                  "The transfer protocol is a hidden exam plus a shutdown dossier, not a magic mode: it never widens what Navin may do, and Navin never writes the claim itself.",
                )}
              </p>
            </div>
            {onOpenAgi ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 shrink-0 rounded-full px-2.5 text-[12px]"
                onClick={onOpenAgi}
                data-testid="dev-guardrails-open-agi"
              >
                {tx("dev.guardrails.openAgi", "Open AGI")}
              </Button>
            ) : null}
          </div>

          <p className="px-1 pb-2 text-[11.5px] leading-5 text-muted-foreground">
            {tx(
              "dev.guardrails.footnote",
              "Project switches live in .navin/board/settings.json, machine switches in the board section of ~/.navin/config.json. Destructive git operations (force-push, hard reset, deletes) always need your approval.",
            )}
          </p>
        </div>
      </div>
    </div>
  );
}

export default DevGuardrailsPanel;
