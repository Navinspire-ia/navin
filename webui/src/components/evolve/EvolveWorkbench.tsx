// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  BadgeCheck,
  BookOpen,
  ChevronDown,
  ChevronUp,
  CircleSlash,
  ClipboardCheck,
  Copy,
  ExternalLink,
  FileDiff,
  FlaskConical,
  GitCommitHorizontal,
  GitPullRequest,
  Power,
  GitBranch,
  GitMerge,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  Square,
  Stethoscope,
  Undo2,
  Trophy,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import MarkdownTextRenderer from "@/components/MarkdownTextRenderer";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { DevProjectSelector } from "@/components/dev/DevProjectSelector";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  cancelEvolveJob,
  enqueueEvolveCampaign,
  fetchEvolveOverview,
  mergeEvolvePromotion,
  publishEvolvePromotion,
  rollbackEvolvePromotion,
  fetchEvolveDocs,
  setEvolveAutorun,
  startEvolveDaemon,
  stopEvolveDaemon,
  verifyEvolveCert,
  type CampaignKind,
  type CertVerification,
  type EvolveOverview,
  type PromotionRecord,
  type VariantOutcome,
} from "@/lib/evolve-api";
import type { RecentProjectEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";
import { evolveLaunchParams } from "./evolveLaunch";
import {
  classifyEvolveFailure,
  daemonAvailability,
  type DaemonAvailability,
  type EvolveFailure,
} from "./evolveStatus";

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };
const POLL_INTERVAL_MS = 5_000;

/** English fallbacks for the failure kinds `classifyEvolveFailure` returns. */
const FAILURE_FALLBACK: Record<EvolveFailure["kind"], string> = {
  unauthorized:
    "This window is no longer signed in to the gateway. Reload it and try again.",
  network: "The Navin gateway is not answering. Check that it is running, then retry.",
  timeout: "The gateway took too long to answer. Retry in a moment.",
  server:
    "The gateway hit an error while reading this project. The details are in the server log.",
  notFound: "This project folder cannot be read from here any more.",
  unknown: "Evolve could not be loaded for this project.",
};

/** What to say when the dashboard loaded but the daemon is not usable. */
const DAEMON_NOTICE: Record<
  Exclude<DaemonAvailability["state"], "online">,
  { titleKey: string; title: string; bodyKey: string; body: string }
> = {
  offline: {
    titleKey: "evolve.daemon.offlineTitle",
    title: "The Evolve daemon is not running",
    bodyKey: "evolve.daemon.offlineBody",
    body: "Start it to prove, optimize and evolve this project. Past results below stay readable without it.",
  },
  // Only an out-of-date gateway still reports this: the daemon speaks a
  // loopback port on every platform since it stopped needing Unix sockets.
  unsupported: {
    titleKey: "evolve.daemon.unsupportedTitle",
    title: "This gateway cannot start the Evolve daemon",
    bodyKey: "evolve.daemon.unsupportedBody",
    body: "Evolve runs on Windows, macOS and Linux, but the gateway serving this project is an older build that only knew Unix sockets. Update Navin, then reload this page.",
  },
  unreachable: {
    titleKey: "evolve.daemon.unreachableTitle",
    title: "The Evolve daemon is not answering",
    bodyKey: "evolve.daemon.unreachableBody",
    body: "A daemon is there but did not answer in time. Stop it and start it again.",
  },
};

interface EvolveWorkbenchProps {
  chatOpen?: boolean;
  projectPath?: string | null;
  projectName?: string | null;
  recentProjects?: RecentProjectEntry[];
  onSelectProject?: (path: string, name?: string) => void;
}

function epochLabel(raw: string): string {
  const value = raw.startsWith("epoch:") ? Number(raw.slice(6)) : Number(raw);
  if (!Number.isFinite(value) || value <= 0) return raw;
  return new Date(value * 1000).toLocaleString();
}

/** Measurements read better rounded: the noise is wider than the digits. */
function round1(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return "-";
  return value.toFixed(1);
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const tone =
    verdict === "pass"
      ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
      : verdict === "weak"
        ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
        : "bg-destructive/15 text-destructive";
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded-full px-2 text-[10.5px] font-semibold uppercase tracking-wide",
        tone,
      )}
    >
      {verdict}
    </span>
  );
}

function GainBadge({ gain }: { gain: number | undefined }) {
  if (gain === undefined) {
    return <span className="text-[11px] text-muted-foreground">-</span>;
  }
  const positive = gain > 0;
  return (
    <span
      className={cn(
        "text-[12px] font-semibold tabular-nums",
        positive
          ? "text-emerald-600 dark:text-emerald-400"
          : "text-destructive",
      )}
    >
      {positive ? "+" : ""}
      {gain.toFixed(1)}%
    </span>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const tone =
    severity === "critical" || severity === "high"
      ? "bg-destructive/15 text-destructive"
      : severity === "medium"
        ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
        : "bg-muted text-muted-foreground";
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-full px-2 text-[10.5px] font-semibold uppercase tracking-wide",
        tone,
      )}
    >
      {severity}
    </span>
  );
}

/** Colour a unified diff the way a reviewer expects to read one. */
function diffTone(line: string): string {
  if (line.startsWith("@@")) return "text-sky-600 dark:text-sky-400";
  if (line.startsWith("+++") || line.startsWith("---")) return "text-muted-foreground";
  if (line.startsWith("diff ") || line.startsWith("index ") || line.startsWith("new file"))
    return "text-muted-foreground";
  if (line.startsWith("+")) return "text-emerald-600 dark:text-emerald-400";
  if (line.startsWith("-")) return "text-destructive";
  return "text-foreground/70";
}

/**
 * The code a candidate actually changed, folded away until asked for. Nobody
 * should have to trust a measurement about a patch they cannot read.
 */
function DiffDisclosure({
  diff,
  showLabel,
  hideLabel,
  copyLabel,
  copiedLabel,
}: {
  diff: string;
  showLabel: string;
  hideLabel: string;
  copyLabel: string;
  copiedLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const lines = useMemo(() => diff.split("\n"), [diff]);
  const changed = useMemo(
    () => ({
      added: lines.filter((line) => line.startsWith("+") && !line.startsWith("+++")).length,
      removed: lines.filter((line) => line.startsWith("-") && !line.startsWith("---")).length,
      files: lines.filter((line) => line.startsWith("diff --git")).length,
    }),
    [lines],
  );

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(diff);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="w-full">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="inline-flex h-6 items-center gap-1 rounded-md border border-border/50 px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
        >
          <FileDiff className="h-3 w-3" aria-hidden />
          {open ? hideLabel : showLabel}
        </button>
        <span className="text-[11px] tabular-nums text-muted-foreground">
          {changed.files > 0 ? `${changed.files} file(s), ` : ""}
          <span className="text-emerald-600 dark:text-emerald-400">+{changed.added}</span>
          {" / "}
          <span className="text-destructive">-{changed.removed}</span>
        </span>
        {open ? (
          <button
            type="button"
            onClick={() => void copy()}
            className="inline-flex h-6 items-center gap-1 rounded-md border border-border/50 px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
          >
            {copied ? (
              <ClipboardCheck className="h-3 w-3" aria-hidden />
            ) : (
              <Copy className="h-3 w-3" aria-hidden />
            )}
            {copied ? copiedLabel : copyLabel}
          </button>
        ) : null}
      </div>
      {open ? (
        <pre className="mt-2 max-h-80 overflow-auto rounded-lg border border-border/40 bg-muted/30 p-2 text-[11px] leading-[1.5]">
          {lines.map((line, index) => (
            <div key={index} className={cn("whitespace-pre font-mono", diffTone(line))}>
              {line || " "}
            </div>
          ))}
        </pre>
      ) : null}
    </div>
  );
}

/**
 * A panel. When `toggle` is given the body is folded away and the header grows
 * a show/hide control, so a dashboard opens quiet and only reveals the detail
 * that was asked for.
 */
function Card({
  title,
  icon,
  children,
  aside,
  open,
  toggle,
  showLabel,
  hideLabel,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  aside?: React.ReactNode;
  open?: boolean;
  toggle?: () => void;
  showLabel?: string;
  hideLabel?: string;
}) {
  const visible = toggle ? !!open : true;
  return (
    <motion.section
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={spring}
      className="rounded-xl border border-border/55 bg-card/60 p-4"
    >
      <div className={cn("flex items-center gap-2", visible && "mb-3")}>
        <span className="text-foreground/70">{icon}</span>
        <h3 className="flex-1 text-[13px] font-semibold text-foreground">{title}</h3>
        {aside}
        {toggle ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={visible}
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-border/50 px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
          >
            {visible ? (
              <ChevronUp className="h-3 w-3" aria-hidden />
            ) : (
              <ChevronDown className="h-3 w-3" aria-hidden />
            )}
            {visible ? hideLabel : showLabel}
          </button>
        ) : null}
      </div>
      {visible ? children : null}
    </motion.section>
  );
}

export function EvolveWorkbench({
  chatOpen,
  projectPath,
  projectName,
  recentProjects,
  onSelectProject,
}: EvolveWorkbenchProps) {
  const { t, i18n } = useTranslation();
  const tx = useCallback(
    (key: string, defaultValue: string, options?: Record<string, unknown>) =>
      t(key, { defaultValue, ...(options ?? {}) }),
    [t],
  );
  const { token } = useClient();

  const [overview, setOverview] = useState<EvolveOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<EvolveFailure | null>(null);
  const [failureDetailOpen, setFailureDetailOpen] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<string | null>(null);
  const [verifications, setVerifications] = useState<Record<string, CertVerification>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const [kind, setKind] = useState<CampaignKind>("proof.run");
  // Results stay folded until asked for, per tab, so a fresh tab is calm.
  const [openResults, setOpenResults] = useState<Record<string, boolean>>({});
  const [startCmd, setStartCmd] = useState("");
  const [urlValue, setUrlValue] = useState("");
  const [profile, setProfile] = useState("quick");
  const [objective, setObjective] = useState("p95");
  const [testCmd, setTestCmd] = useState("");
  const [preset, setPreset] = useState("");
  /** Proofs include uncommitted file edits (chat review / working tree). */
  const [includeDirty, setIncludeDirty] = useState(true);

  const [docsOpen, setDocsOpen] = useState(false);
  const [docsMarkdown, setDocsMarkdown] = useState<string | null>(null);

  const pollRef = useRef<number | null>(null);
  const openDocs = useCallback(() => {
    setDocsOpen(true);
    if (docsMarkdown || !token) return;
    fetchEvolveDocs(token, i18n.language || "en")
      .then((payload) => setDocsMarkdown(payload.markdown))
      .catch((err) =>
        setDocsMarkdown(err instanceof Error ? err.message : String(err)),
      );
  }, [docsMarkdown, token, i18n.language]);

  const load = useCallback(
    async (silent = false) => {
      if (!token || !projectPath) return;
      if (!silent) setLoading(true);
      try {
        const data = await fetchEvolveOverview(token, projectPath);
        setOverview(data);
        setPreset((current) => current || data.default_preset || "");
        setFailure(null);
      } catch (err) {
        // A background poll must not replace a working dashboard with an
        // error; only an explicit load reports one.
        if (!silent) setFailure(classifyEvolveFailure(err));
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [token, projectPath],
  );

  useEffect(() => {
    setOverview(null);
    setVerifications({});
    setNotice(null);
    setFailureDetailOpen(false);
    void load();
  }, [load]);

  useEffect(() => {
    pollRef.current = window.setInterval(() => void load(true), POLL_INTERVAL_MS);
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, [load]);

  const daemon = daemonAvailability(overview?.daemon);
  const daemonOnline = daemon.state === "online";
  // Nothing to start on a host that cannot host one.
  const daemonStartable = daemon.state === "offline" || daemon.state === "unreachable";
  // Each tab reports on its own operation only: an evolve run has nothing to
  // say on the Prove tab.
  const jobs = (overview?.daemon.status?.jobs ?? []).filter((job) => job.kind === kind);
  const activeJobs = jobs.filter((job) => job.state === "running" || job.state === "queued");
  const latestProof = overview?.proofs[0];
  const latestDiagnosis = overview?.diagnoses?.[0];
  const latestOptimize = overview?.optimize_runs[0];
  const latestEvolveRun = overview?.evolve_runs[0];
  const promotions = overview?.promotions ?? [];

  const runAction = useCallback(
    async (
      key: string,
      action: () => Promise<unknown>,
      successMessage: string,
    ) => {
      setBusyAction(key);
      setNotice(null);
      try {
        await action();
        setNotice(successMessage);
        await load(true);
      } catch (err) {
        // The gateway's own wording is worth keeping - it names the daemon,
        // the branch, the promotion. Library boilerplate is not, and comes
        // back with an empty detail.
        const problem = classifyEvolveFailure(err);
        setNotice(
          problem.detail || tx(`evolve.error.${problem.kind}`, FAILURE_FALLBACK[problem.kind]),
        );
      } finally {
        setBusyAction(null);
        setConfirmAction(null);
      }
    },
    [load, tx],
  );

  const onVerify = useCallback(
    async (record: PromotionRecord) => {
      if (!token || !projectPath) return;
      setBusyAction(`verify:${record.id}`);
      try {
        const result = await verifyEvolveCert(token, projectPath, record.id);
        setVerifications((current) => ({ ...current, [record.id]: result }));
      } catch (err) {
        setNotice(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyAction(null);
      }
    },
    [token, projectPath],
  );

  const onLaunch = useCallback(async () => {
    if (!token || !projectPath) return;
    const params = evolveLaunchParams(kind, {
      start: startCmd,
      url: urlValue,
      test: testCmd,
      objective,
      profile,
      preset,
      includeDirty,
    });
    await runAction(
      "launch",
      () => enqueueEvolveCampaign(token, projectPath, kind, params),
      tx("evolve.launch.queued", "Operation queued; results will appear below."),
    );
  }, [token, projectPath, kind, startCmd, urlValue, testCmd, objective, profile, preset, includeDirty, runAction, tx]);

  const toggleResults = useCallback((panel: string) => {
    setOpenResults((current) => ({ ...current, [panel]: !current[panel] }));
  }, []);

  const kindOptions: Array<{
    id: CampaignKind;
    label: string;
    hint: string;
    title: string;
    icon: React.ReactNode;
  }> = useMemo(
    () => [
      {
        id: "proof.run",
        label: tx("evolve.kind.proof", "Prove"),
        hint: tx("evolve.kind.proofHint", "Inject faults, score robustness"),
        title: tx("evolve.kind.proofTitle", "Prove robustness"),
        icon: <ShieldCheck className="h-3.5 w-3.5" aria-hidden />,
      },
      {
        id: "optimize.run",
        label: tx("evolve.kind.optimize", "Optimize"),
        hint: tx("evolve.kind.optimizeHint", "Measure candidates against the baseline"),
        title: tx("evolve.kind.optimizeTitle", "Optimize performance"),
        icon: <Trophy className="h-3.5 w-3.5" aria-hidden />,
      },
      {
        id: "evolve.run",
        label: tx("evolve.kind.evolve", "Evolve"),
        hint: tx("evolve.kind.evolveHint", "Turn findings into promoted fixes"),
        title: tx("evolve.kind.evolveTitle", "Evolve the code"),
        icon: <FlaskConical className="h-3.5 w-3.5" aria-hidden />,
      },
    ],
    [tx],
  );

  const failureMessage = failure
    ? tx(`evolve.error.${failure.kind}`, FAILURE_FALLBACK[failure.kind])
    : "";
  const daemonNotice = daemonOnline ? null : DAEMON_NOTICE[daemon.state];

  const activeKind = kindOptions.find((option) => option.id === kind) ?? kindOptions[0];
  const showLabel = tx("evolve.results.show", "Show results");
  const hideLabel = tx("evolve.results.hide", "Hide");
  const diffShowLabel = tx("evolve.diff.show", "Show the code change");
  const diffHideLabel = tx("evolve.diff.hide", "Hide the code change");
  const diffCopyLabel = tx("evolve.diff.copy", "Copy the diff");
  const diffCopiedLabel = tx("evolve.diff.copied", "Copied");

  const variantRow = (variant: VariantOutcome, winner?: string) => (
    <div
      key={variant.candidate_id}
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border/40 px-3 py-2",
        winner === variant.candidate_id && "border-emerald-500/50 bg-emerald-500/5",
      )}
    >
      {winner === variant.candidate_id ? (
        <Trophy className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
      ) : null}
      <span className="text-[12px] font-medium text-foreground">{variant.candidate_id}</span>
      <GainBadge gain={variant.gain_percent} />
      {variant.significant === false ? (
        <span className="text-[11px] text-amber-600 dark:text-amber-400">
          {tx("evolve.variant.noise", "within noise")}
        </span>
      ) : null}
      {variant.tests_passed !== undefined ? (
        <span
          className={cn(
            "text-[11px]",
            variant.tests_passed
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-destructive",
          )}
        >
          {variant.tests_passed
            ? tx("evolve.variant.testsPass", "tests ok")
            : tx("evolve.variant.testsFail", "tests broken")}
        </span>
      ) : null}
      {variant.invariants_ok !== undefined ? (
        <span
          className={cn(
            "text-[11px]",
            variant.invariants_ok
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-destructive",
          )}
        >
          {variant.invariants_ok
            ? tx("evolve.variant.invariantsPass", "invariants ok")
            : tx("evolve.variant.invariantsFail", "invariants broken")}
        </span>
      ) : null}
      {variant.behavior_equivalent !== undefined ? (
        <span
          className={cn(
            "text-[11px]",
            variant.behavior_equivalent
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-destructive",
          )}
        >
          {variant.behavior_equivalent
            ? tx("evolve.variant.behaviorSame", "behavior preserved")
            : tx("evolve.variant.behaviorDiff", "behavior changed")}
        </span>
      ) : null}
      {!variant.eligible ? (
        <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
          <CircleSlash className="h-3 w-3" aria-hidden />
          {tx("evolve.variant.rejected", "rejected")}
        </span>
      ) : null}
      <span className="w-full truncate text-[11px] text-muted-foreground" title={variant.note}>
        {variant.note || variant.rationale}
      </span>
      {variant.diff ? (
        <DiffDisclosure
          diff={variant.diff}
          showLabel={diffShowLabel}
          hideLabel={diffHideLabel}
          copyLabel={diffCopyLabel}
          copiedLabel={diffCopiedLabel}
        />
      ) : null}
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-background" data-testid="evolve-workbench">
      <header
        className={cn(
          "flex h-11 shrink-0 items-center gap-2 border-b border-border/55 px-3",
          !chatOpen && NOTIFICATION_GUTTER,
        )}
      >
        <FlaskConical className="h-4 w-4 shrink-0 text-foreground/80" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-semibold text-foreground">
            {tx("evolve.title", "Evolve Engine")}
          </p>
          <p className="truncate text-[11px] text-muted-foreground">
            {projectName || projectPath || tx("evolve.noProject", "No project selected")}
          </p>
        </div>
        <span
          className={cn(
            "inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-[11px] font-medium",
            daemonOnline
              ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
              : "bg-muted text-muted-foreground",
          )}
        >
          <Activity className="h-3 w-3" aria-hidden />
          {daemonOnline
            ? tx("evolve.daemon.online", "daemon online")
            : daemon.state === "unsupported"
              ? tx("evolve.daemon.unavailable", "daemon unavailable")
              : tx("evolve.daemon.offline", "daemon offline")}
        </span>
        {daemonStartable && projectPath && overview ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 shrink-0 px-2.5 text-[11px]"
            disabled={busyAction === "daemon" || !overview.engine_bin}
            title={
              overview.engine_bin
                ? tx("evolve.launch.startDaemon", "Start the daemon")
                : tx(
                    "evolve.launch.needDaemon",
                    "Start the daemon first: navin-engine daemon {{path}}",
                    { path: projectPath },
                  )
            }
            onClick={() => {
              if (!token) return;
              void runAction(
                "daemon",
                () => startEvolveDaemon(token, projectPath),
                tx("evolve.launch.daemonStarted", "Daemon started for this project."),
              );
            }}
          >
            {busyAction === "daemon" ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <Activity className="mr-1 h-3 w-3" aria-hidden />
            )}
            {tx("evolve.launch.startDaemon", "Start the daemon")}
          </Button>
        ) : null}
        {daemonOnline && projectPath ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 w-8 shrink-0 p-0 text-muted-foreground hover:text-destructive"
            disabled={busyAction === "daemon-stop"}
            title={tx("evolve.daemon.stop", "Stop the daemon")}
            aria-label={tx("evolve.daemon.stop", "Stop the daemon")}
            onClick={() => {
              if (!token) return;
              void runAction(
                "daemon-stop",
                () => stopEvolveDaemon(token, projectPath),
                tx("evolve.daemon.stopped", "Daemon stopped."),
              );
            }}
          >
            {busyAction === "daemon-stop" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Power className="h-3.5 w-3.5" aria-hidden />
            )}
          </Button>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-8 w-8 shrink-0 p-0"
          onClick={openDocs}
          title={tx("evolve.docs.open", "Documentation")}
          aria-label={tx("evolve.docs.open", "Documentation")}
        >
          <BookOpen className="h-3.5 w-3.5" aria-hidden />
        </Button>
        {onSelectProject ? (
          <DevProjectSelector
            projectPath={projectPath ?? null}
            projectName={projectName ?? null}
            recentProjects={recentProjects ?? []}
            onSelectProject={onSelectProject}
            compact
          />
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-8 w-8 shrink-0 p-0"
          onClick={() => void load()}
          disabled={loading}
          title={tx("evolve.refresh", "Refresh")}
          aria-label={tx("evolve.refresh", "Refresh")}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
        </Button>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {!projectPath ? (
          <p className="p-6 text-center text-sm text-muted-foreground">
            {tx("evolve.pickProject", "Pick a project to inspect its Evolve history.")}
          </p>
        ) : failure ? (
          <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={spring}
            className="rounded-xl border border-destructive/40 bg-destructive/5 p-4"
          >
            <div className="flex items-start gap-2.5">
              <AlertTriangle
                className="mt-0.5 h-4 w-4 shrink-0 text-destructive"
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-semibold text-foreground">
                  {tx("evolve.error.title", "Evolve could not be loaded")}
                </p>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">
                  {failureMessage}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 px-2.5 text-[11px]"
                    disabled={loading}
                    onClick={() => void load()}
                  >
                    <RefreshCw
                      className={cn("mr-1 h-3 w-3", loading && "animate-spin")}
                      aria-hidden
                    />
                    {tx("evolve.error.retry", "Retry")}
                  </Button>
                  {failure.detail ? (
                    <button
                      type="button"
                      onClick={() => setFailureDetailOpen((value) => !value)}
                      aria-expanded={failureDetailOpen}
                      className="inline-flex h-7 items-center gap-1 rounded-md border border-border/50 px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
                    >
                      {failureDetailOpen ? (
                        <ChevronUp className="h-3 w-3" aria-hidden />
                      ) : (
                        <ChevronDown className="h-3 w-3" aria-hidden />
                      )}
                      {tx("evolve.error.details", "Technical details")}
                    </button>
                  ) : null}
                </div>
                {failureDetailOpen && failure.detail ? (
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-border/40 bg-muted/30 p-2 font-mono text-[11px] leading-[1.5] text-muted-foreground">
                    {failure.detail}
                  </pre>
                ) : null}
              </div>
            </div>
          </motion.section>
        ) : null}

        {notice ? (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="rounded-lg border border-border/50 bg-accent/30 px-3 py-2 text-[12px] text-foreground"
          >
            {notice}
          </motion.p>
        ) : null}

        {projectPath && overview ? (
          <>
            <div
              role="tablist"
              aria-label={tx("evolve.tabs.label", "Operations")}
              className="flex gap-1 rounded-xl border border-border/55 bg-card/60 p-1"
            >
              {kindOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  role="tab"
                  id={`evolve-tab-${option.id}`}
                  aria-selected={kind === option.id}
                  aria-controls={`evolve-panel-${option.id}`}
                  onClick={() => setKind(option.id)}
                  className={cn(
                    "flex-1 rounded-lg px-3 py-2 text-left transition-colors",
                    kind === option.id
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
                  )}
                >
                  <span className="flex items-center gap-1.5 text-[12px] font-semibold">
                    {option.icon}
                    {option.label}
                  </span>
                  <span
                    className={cn(
                      "mt-0.5 block text-[10.5px] leading-tight",
                      kind === option.id ? "text-background/70" : "text-muted-foreground/80",
                    )}
                  >
                    {option.hint}
                  </span>
                </button>
              ))}
            </div>

            <div
              role="tabpanel"
              id={`evolve-panel-${kind}`}
              aria-labelledby={`evolve-tab-${kind}`}
              className="space-y-3"
            >
              {daemonNotice ? (
                <motion.section
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={spring}
                  className="rounded-xl border border-border/55 bg-card/60 p-4"
                  data-testid="evolve-daemon-notice"
                >
                  <div className="flex items-start gap-2.5">
                    <CircleSlash
                      className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-semibold text-foreground">
                        {tx(daemonNotice.titleKey, daemonNotice.title)}
                      </p>
                      <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">
                        {tx(daemonNotice.bodyKey, daemonNotice.body)}
                      </p>
                      {daemonStartable ? (
                        <div className="mt-2.5 flex flex-wrap items-center gap-2">
                          <Button
                            type="button"
                            size="sm"
                            className="h-7 px-2.5 text-[11px]"
                            disabled={busyAction === "daemon" || !overview.engine_bin}
                            onClick={() => {
                              if (!token) return;
                              void runAction(
                                "daemon",
                                () => startEvolveDaemon(token, projectPath),
                                tx(
                                  "evolve.launch.daemonStarted",
                                  "Daemon started for this project.",
                                ),
                              );
                            }}
                          >
                            {busyAction === "daemon" ? (
                              <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                            ) : (
                              <Activity className="mr-1 h-3 w-3" aria-hidden />
                            )}
                            {tx("evolve.launch.startDaemon", "Start the daemon")}
                          </Button>
                          {!overview.engine_bin ? (
                            <span className="text-[11px] text-muted-foreground">
                              {tx(
                                "evolve.launch.needDaemon",
                                "Start the daemon first: navin-engine daemon {{path}}",
                                { path: projectPath },
                              )}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </div>
                </motion.section>
              ) : null}

              <Card
                title={activeKind.title}
                icon={activeKind.icon}
                aside={
                  activeJobs.length ? (
                    <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                      {tx("evolve.launch.running", "{{count}} job(s) in flight", {
                        count: activeJobs.length,
                      })}
                    </span>
                  ) : null
                }
              >
                <div className="flex flex-wrap items-start gap-2">
                  <div className="flex w-56 flex-col gap-1">
                    <Input
                      value={startCmd}
                      onChange={(event) => setStartCmd(event.target.value)}
                      placeholder={
                        overview.hint?.start
                          ? tx("evolve.launch.auto", "Auto: {{cmd}}", {
                              cmd: overview.hint.start,
                            })
                          : tx("evolve.launch.start", "Start command (optional)")
                      }
                      className="h-8 text-[12px]"
                    />
                    <p className="px-1 text-[10px] leading-tight text-muted-foreground">
                      {tx(
                        "evolve.launch.startCaption",
                        "How the app starts - empty: the engine works it out",
                      )}
                    </p>
                  </div>
                  <div className="flex w-52 flex-col gap-1">
                    <Input
                      value={urlValue}
                      onChange={(event) => setUrlValue(event.target.value)}
                      placeholder={
                        overview?.hint?.url
                          ? tx("evolve.launch.auto", "Auto: {{cmd}}", {
                              cmd: overview.hint.url,
                            })
                          : tx("evolve.launch.url", "Probe URL (optional)")
                      }
                      className="h-8 text-[12px]"
                    />
                    <p className="px-1 text-[10px] leading-tight text-muted-foreground">
                      {tx(
                        "evolve.launch.urlCaption",
                        "Where to probe - empty: found by booting the app",
                      )}
                    </p>
                  </div>
                  {kind === "optimize.run" ? (
                    <div className="flex gap-0.5 rounded-lg border border-border/50 p-0.5">
                      {["p95", "throughput"].map((option) => (
                        <button
                          key={option}
                          type="button"
                          onClick={() => setObjective(option)}
                          className={cn(
                            "h-7 rounded-md px-2.5 text-[11.5px] font-medium transition-colors",
                            objective === option
                              ? "bg-foreground text-background"
                              : "text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {option}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="flex gap-0.5 rounded-lg border border-border/50 p-0.5">
                      {["quick", "standard", "deep"].map((option) => (
                        <button
                          key={option}
                          type="button"
                          onClick={() => setProfile(option)}
                          className={cn(
                            "h-7 rounded-md px-2.5 text-[11.5px] font-medium transition-colors",
                            profile === option
                              ? "bg-foreground text-background"
                              : "text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {option}
                        </button>
                      ))}
                    </div>
                  )}
                  {kind !== "proof.run" ? (
                    <div className="flex w-52 flex-col gap-1">
                      <Input
                        value={testCmd}
                        onChange={(event) => setTestCmd(event.target.value)}
                        placeholder={
                          overview.hint?.test
                            ? tx("evolve.launch.auto", "Auto: {{cmd}}", {
                                cmd: overview.hint.test,
                              })
                            : tx("evolve.launch.test", "Test command (optional)")
                        }
                        className="h-8 text-[12px]"
                      />
                      <p className="px-1 text-[10px] leading-tight text-muted-foreground">
                        {tx(
                          "evolve.launch.testCaption",
                          "Project test suite - empty: auto-detected",
                        )}
                      </p>
                    </div>
                  ) : null}
                  {kind !== "proof.run" ? (
                    <select
                      value={preset}
                      onChange={(event) => setPreset(event.target.value)}
                      title={tx("evolve.launch.modelHint", "LLM used to generate candidates")}
                      className="h-8 rounded-md border border-input bg-background px-2 text-[12px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="">
                        {tx("evolve.launch.modelDefault", "Model: config default")}
                      </option>
                      {(overview.model_presets ?? []).map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  ) : null}
                  {kind === "proof.run" ? (
                    <label className="flex h-8 cursor-pointer items-center gap-1.5 px-1 text-[11.5px] text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={includeDirty}
                        onChange={(event) => setIncludeDirty(event.target.checked)}
                        className="h-3.5 w-3.5 accent-foreground"
                      />
                      {tx(
                        "evolve.launch.includeDirty",
                        "Include uncommitted file edits",
                      )}
                    </label>
                  ) : null}
                  <Button
                    type="button"
                    size="sm"
                    className="h-8"
                    disabled={!daemonOnline || busyAction === "launch"}
                    onClick={() => void onLaunch()}
                  >
                    {busyAction === "launch" ? (
                      <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Play className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                    )}
                    {tx("evolve.launch.go", "Launch")}
                  </Button>
                </div>
                <button
                  type="button"
                  onClick={openDocs}
                  className="mt-1.5 inline-flex items-center gap-1.5 px-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                >
                  <BookOpen className="h-3 w-3" aria-hidden />
                  {tx(
                    "evolve.launch.examples",
                    "Docs: command, test and URL examples per stack",
                  )}
                </button>
                <div
                  className={cn(
                    "mt-3 flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                    overview.autorun?.enabled
                      ? "border-emerald-500/40 bg-emerald-500/10"
                      : "border-border/60 bg-muted/30",
                  )}
                >
                  <div className="flex min-w-0 items-start gap-2.5">
                    <GitCommitHorizontal
                      className={cn(
                        "mt-0.5 h-4 w-4 shrink-0",
                        overview.autorun?.enabled
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-muted-foreground",
                      )}
                      aria-hidden
                    />
                    <div className="min-w-0">
                      <p className="text-[12px] font-semibold text-foreground">
                        {tx("evolve.autorun.label", "Auto-run on commit")}
                      </p>
                      <p className="text-[11px] leading-relaxed text-muted-foreground">
                        {overview.autorun?.enabled && overview.autorun?.kind
                          ? tx(
                              "evolve.autorun.active",
                              "Each commit reruns {{kind}} in the background.",
                              { kind: overview.autorun.kind },
                            )
                          : tx(
                              "evolve.autorun.hint",
                              "The daemon watches your commits and replays the last operation.",
                            )}
                      </p>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span
                      className={cn(
                        "text-[11px] font-semibold",
                        overview.autorun?.enabled
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-muted-foreground",
                      )}
                    >
                      {busyAction === "autorun"
                        ? "…"
                        : overview.autorun?.enabled
                          ? tx("evolve.autorun.on", "Enabled")
                          : tx("evolve.autorun.off", "Disabled")}
                    </span>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={!!overview.autorun?.enabled}
                      aria-label={tx("evolve.autorun.label", "Auto-run on commit")}
                      disabled={busyAction === "autorun"}
                      onClick={() => {
                        if (!token || !projectPath) return;
                        void runAction(
                          "autorun",
                          () =>
                            setEvolveAutorun(
                              token,
                              projectPath,
                              !overview.autorun?.enabled,
                            ),
                          overview.autorun?.enabled
                            ? tx("evolve.autorun.disabledMsg", "Auto-run on commit disabled.")
                            : tx(
                                "evolve.autorun.enabledMsg",
                                "Auto-run enabled: each new commit reruns the last operation.",
                              ),
                        );
                      }}
                      className={cn(
                        "relative h-6 w-11 rounded-full transition-colors duration-200 disabled:opacity-60",
                        overview.autorun?.enabled ? "bg-emerald-500" : "bg-muted-foreground/30",
                      )}
                    >
                      <motion.span
                        layout
                        transition={spring}
                        className={cn(
                          "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm",
                          overview.autorun?.enabled ? "left-[22px]" : "left-0.5",
                        )}
                      />
                    </button>
                  </div>
                </div>
                {jobs.length ? (
                  <div className="mt-3 space-y-1">
                    {jobs.slice(-4).map((job) => {
                      const stoppable = job.state === "running" || job.state === "queued";
                      return (
                        <div
                          key={job.id}
                          className="flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground"
                        >
                          <span>#{job.id}</span>
                          <span
                            className={cn(
                              "font-medium",
                              job.state === "completed" &&
                                "text-emerald-600 dark:text-emerald-400",
                              job.state === "failed" && "text-destructive",
                              job.state === "cancelled" && "text-amber-600 dark:text-amber-400",
                            )}
                          >
                            {job.state}
                          </span>
                          {job.detail ? <span>{job.detail}</span> : null}
                          {stoppable ? (
                            <button
                              type="button"
                              disabled={busyAction === `job-cancel:${job.id}`}
                              onClick={() => {
                                if (!token) return;
                                void runAction(
                                  `job-cancel:${job.id}`,
                                  () => cancelEvolveJob(token, projectPath, job.id),
                                  tx("evolve.jobs.stopped", "Job #{{id}} stopped.", {
                                    id: job.id,
                                  }),
                                );
                              }}
                              className="inline-flex items-center gap-1 rounded-md border border-border/50 px-1.5 py-0.5 font-medium text-foreground transition-colors hover:bg-accent/40 disabled:opacity-60"
                              title={tx("evolve.jobs.stop", "Stop this job")}
                            >
                              {busyAction === `job-cancel:${job.id}` ? (
                                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                              ) : (
                                <Square className="h-2.5 w-2.5" aria-hidden />
                              )}
                              {tx("evolve.jobs.stop", "Stop this job")}
                            </button>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </Card>

              {kind === "proof.run" ? (
                <Card
                  title={tx("evolve.proof.title", "Robustness proof")}
                  icon={<ShieldCheck className="h-4 w-4" aria-hidden />}
                  open={openResults["proof.run"]}
                  toggle={() => toggleResults("proof.run")}
                  showLabel={showLabel}
                  hideLabel={hideLabel}
                  aside={
                    latestProof ? (
                      <span className="flex items-center gap-2">
                        <VerdictBadge verdict={latestProof.verdict} />
                        <span className="text-[15px] font-bold tabular-nums text-foreground">
                          {latestProof.robustness_score}
                          <span className="text-[11px] font-normal text-muted-foreground">
                            /100
                          </span>
                        </span>
                      </span>
                    ) : (
                      <span className="text-[11px] text-muted-foreground">
                        {tx("evolve.proof.none", "nothing proved yet")}
                      </span>
                    )
                  }
                >
                  {!latestProof ? (
                    <p className="text-[12px] text-muted-foreground">
                      {tx(
                        "evolve.proof.empty",
                        "No proof recorded yet. Launch a proof run above.",
                      )}
                    </p>
                  ) : (
                    <>
                      <p className="mb-2 text-[11px] text-muted-foreground">
                        {tx("evolve.proof.meta", "Profile {{profile}}, {{when}}", {
                          profile: latestProof.profile,
                          when: epochLabel(latestProof.collected_at),
                        })}
                      </p>
                      <div className="space-y-1.5">
                        {latestProof.faults.map((fault) => (
                          <div
                            key={fault.fault}
                            className="flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded-lg border border-border/40 px-3 py-1.5"
                          >
                            <span className="text-[12px] font-medium text-foreground">
                              {fault.fault}
                            </span>
                            <VerdictBadge verdict={fault.verdict} />
                            <span className="text-[11px] text-muted-foreground">
                              {fault.description}
                            </span>
                            {fault.evidence?.length ? (
                              <span className="w-full text-[11px] text-muted-foreground/80">
                                {fault.evidence.join(" | ")}
                              </span>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </Card>
              ) : null}

              {kind !== "optimize.run" ? (
                <Card
                  title={tx("evolve.findings.title", "Problems found")}
                  icon={<Stethoscope className="h-4 w-4" aria-hidden />}
                  open={openResults.findings}
                  toggle={() => toggleResults("findings")}
                  showLabel={showLabel}
                  hideLabel={hideLabel}
                  aside={
                    <span className="text-[11px] text-muted-foreground">
                      {latestDiagnosis
                        ? tx("evolve.findings.count", "{{count}} diagnosed", {
                            count: latestDiagnosis.findings.length,
                          })
                        : tx("evolve.findings.none", "nothing diagnosed yet")}
                    </span>
                  }
                >
                  {!latestDiagnosis ? (
                    <p className="text-[12px] text-muted-foreground">
                      {tx(
                        "evolve.findings.empty",
                        "No diagnosis recorded yet. A proof or evolve run produces one.",
                      )}
                    </p>
                  ) : latestDiagnosis.findings.length === 0 ? (
                    <p className="text-[12px] text-muted-foreground">{latestDiagnosis.summary}</p>
                  ) : (
                    <div className="space-y-1.5">
                      {latestDiagnosis.findings.map((finding) => (
                        <div
                          key={finding.id}
                          className="rounded-lg border border-border/40 px-3 py-2"
                        >
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            <SeverityBadge severity={finding.severity} />
                            <span className="text-[12px] font-medium text-foreground">
                              {finding.title}
                            </span>
                            <span className="font-mono text-[11px] text-muted-foreground">
                              {finding.id}
                            </span>
                            <span className="ml-auto text-[11px] text-muted-foreground">
                              {tx("evolve.findings.confidence", "{{level}} confidence", {
                                level: finding.confidence,
                              })}
                            </span>
                          </div>
                          <p className="mt-1 text-[11px] text-foreground/80">{finding.symptom}</p>
                          <p className="mt-0.5 text-[11px] text-muted-foreground">
                            {tx("evolve.findings.cause", "Likely cause: {{cause}}", {
                              cause: finding.root_cause,
                            })}
                          </p>
                          <p className="mt-0.5 text-[11px] text-muted-foreground">
                            {tx("evolve.findings.remedy", "Direction: {{remedy}}", {
                              remedy: finding.remediation,
                            })}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              ) : null}

              {kind === "optimize.run" ? (
                <Card
                  title={tx("evolve.optimize.title", "Optimization run (ASSE)")}
                  icon={<Trophy className="h-4 w-4" aria-hidden />}
                  open={openResults["optimize.run"]}
                  toggle={() => toggleResults("optimize.run")}
                  showLabel={showLabel}
                  hideLabel={hideLabel}
                  aside={
                    !latestOptimize ? (
                      <span className="text-[11px] text-muted-foreground">
                        {tx("evolve.optimize.none", "nothing measured yet")}
                      </span>
                    ) : latestOptimize.winner ? (
                      <span className="text-[12px] font-semibold text-emerald-600 dark:text-emerald-400">
                        {latestOptimize.winner}{" "}
                        {latestOptimize.winner_gain_percent !== undefined
                          ? `+${latestOptimize.winner_gain_percent.toFixed(1)}%`
                          : ""}
                      </span>
                    ) : (
                      <span className="text-[11px] text-muted-foreground">
                        {tx("evolve.optimize.noWinner", "no measured winner")}
                      </span>
                    )
                  }
                >
                  {!latestOptimize ? (
                    <p className="text-[12px] text-muted-foreground">
                      {tx(
                        "evolve.optimize.empty",
                        "No optimization run recorded yet. Launch one above.",
                      )}
                    </p>
                  ) : (
                    <>
                      <p className="mb-2 text-[11px] text-muted-foreground">
                        {tx(
                          "evolve.optimize.baseline",
                          "Objective {{objective}}. Baseline: P95 {{p95}} ms, {{rps}} RPS.",
                          {
                          objective: latestOptimize.objective,
                          p95:
                            latestOptimize.baseline_p95_std_ms !== undefined
                              ? `${round1(latestOptimize.baseline.p95_ms)} ± ${round1(latestOptimize.baseline_p95_std_ms)}`
                              : round1(latestOptimize.baseline.p95_ms),
                          rps:
                            latestOptimize.baseline_rps_std !== undefined
                              ? `${round1(latestOptimize.baseline.rps)} ± ${round1(latestOptimize.baseline_rps_std)}`
                              : round1(latestOptimize.baseline.rps),
                          },
                        )}
                        {latestOptimize.bench_repeats && latestOptimize.bench_repeats > 1
                          ? " " +
                            tx(
                              "evolve.optimize.repeats",
                              "{{count}} benchmark windows per measurement.",
                              { count: latestOptimize.bench_repeats },
                            )
                          : ""}
                        {latestOptimize.invariants_checked
                          ? " " +
                            tx(
                              "evolve.optimize.invariants",
                              "{{count}} business invariants checked.",
                              { count: latestOptimize.invariants_checked },
                            )
                          : ""}
                      </p>
                      {latestOptimize.variants.length ? (
                        <div className="space-y-1.5">
                          {latestOptimize.variants.map((variant) =>
                            variantRow(variant, latestOptimize.winner),
                          )}
                        </div>
                      ) : (
                        <p className="text-[12px] text-muted-foreground">
                          {tx(
                            "evolve.optimize.noVariant",
                            "No candidate was generated for this run.",
                          )}
                        </p>
                    )}
                  </>
                  )}
                </Card>
              ) : null}

              {kind === "evolve.run" ? (
                <Card
                  title={tx("evolve.run.title", "Evolve run")}
                  icon={<FlaskConical className="h-4 w-4" aria-hidden />}
                  open={openResults["evolve.run"]}
                  toggle={() => toggleResults("evolve.run")}
                  showLabel={showLabel}
                  hideLabel={hideLabel}
                  aside={
                    <span className="text-[11px] text-muted-foreground">
                      {latestEvolveRun
                        ? epochLabel(latestEvolveRun.collected_at)
                        : tx("evolve.run.none", "nothing evolved yet")}
                    </span>
                  }
                >
                  {!latestEvolveRun ? (
                    <p className="text-[12px] text-muted-foreground">
                      {tx("evolve.run.empty", "No evolve run recorded yet. Launch one above.")}
                    </p>
                  ) : (
                    <>
                      <p className="text-[12px] text-foreground">
                        {tx(
                          "evolve.run.summary",
                          "Robustness {{score}}/100, {{total}} finding(s), {{addressed}} addressed.",
                          {
                            score: latestEvolveRun.robustness_before,
                            total: latestEvolveRun.findings_total,
                            addressed: latestEvolveRun.findings_addressed,
                          },
                        )}
                      </p>
                      {latestEvolveRun.outcomes?.length ? (
                        <div className="mt-2 space-y-1.5">
                          {latestEvolveRun.outcomes.map((outcome) => (
                            <div
                              key={outcome.finding}
                              className="flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded-lg border border-border/40 px-3 py-1.5"
                            >
                              <span className="text-[12px] font-medium text-foreground">
                                {outcome.title || outcome.finding}
                              </span>
                              <VerdictBadge verdict={outcome.accepted ? "pass" : "weak"} />
                              {outcome.note ? (
                                <span className="text-[11px] text-muted-foreground">
                                  {outcome.note}
                                </span>
                              ) : null}
                            </div>
                          ))}
                        </div>
                    ) : null}
                  </>
                  )}
                </Card>
              ) : null}

              <Card
                title={tx("evolve.promotions.title", "Promotions")}
                icon={<GitBranch className="h-4 w-4" aria-hidden />}
                open={openResults.promotions}
                toggle={() => toggleResults("promotions")}
                showLabel={showLabel}
                hideLabel={hideLabel}
                aside={
                  promotions.length ? (
                    <span className="text-[11px] text-muted-foreground">
                      {tx("evolve.promotions.count", "{{count}} recorded", {
                        count: promotions.length,
                      })}
                    </span>
                  ) : (
                    <span className="text-[11px] text-muted-foreground">
                      {tx("evolve.promotions.none", "none yet")}
                    </span>
                  )
                }
              >
                {promotions.length === 0 ? (
                  <p className="text-[12px] text-muted-foreground">
                    {tx("evolve.promotions.empty", "No promotion recorded yet.")}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {promotions.map((record) => {
                      const verification = verifications[record.id];
                      const canMerge =
                        record.outcome === "branch_only" &&
                        !record.merged &&
                        !record.rolled_back_at;
                      const canRollback = !record.rolled_back_at && record.outcome !== "blocked";
                      // Review happens on the forge, so a merged branch is
                      // still worth pushing.
                      const canPublish = !!record.branch && !record.rolled_back_at;
                      return (
                        <div
                          key={record.id}
                          className="rounded-lg border border-border/40 px-3 py-2"
                        >
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            <span className="text-[12px] font-medium text-foreground">
                              {record.candidate_id}
                            </span>
                            <span className="text-[11px] text-muted-foreground">
                              {record.finding}
                            </span>
                            <VerdictBadge
                              verdict={
                                record.rolled_back_at
                                  ? "fail"
                                  : record.outcome === "merged"
                                    ? "pass"
                                    : record.outcome === "branch_only"
                                      ? "weak"
                                      : "fail"
                              }
                            />
                            <span className="text-[11px] text-muted-foreground">
                              {record.rolled_back_at
                                ? tx("evolve.promotions.rolledBack", "rolled back")
                                : record.outcome === "merged"
                                  ? tx("evolve.promotions.merged", "merged")
                                  : record.outcome === "branch_only"
                                    ? record.branch ||
                                      tx("evolve.promotions.branch", "branch ready")
                                    : tx("evolve.promotions.blocked", "blocked")}
                            </span>
                            <span className="ml-auto text-[11px] text-muted-foreground">
                              {epochLabel(record.created_at)}
                            </span>
                          </div>
                          <div className="mt-1.5 flex flex-wrap items-center gap-2">
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="h-7 px-2 text-[11px]"
                              disabled={busyAction === `verify:${record.id}`}
                              onClick={() => void onVerify(record)}
                            >
                              {busyAction === `verify:${record.id}` ? (
                                <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                              ) : (
                                <BadgeCheck className="mr-1 h-3 w-3" aria-hidden />
                              )}
                              {tx("evolve.promotions.verify", "Verify certificate")}
                            </Button>
                            {verification ? (
                              <span
                                className={cn(
                                  "inline-flex items-center gap-1 text-[11px] font-medium",
                                  verification.authentic
                                    ? "text-emerald-600 dark:text-emerald-400"
                                    : "text-destructive",
                                )}
                              >
                                {verification.authentic ? (
                                  <ShieldCheck className="h-3 w-3" aria-hidden />
                                ) : (
                                  <ShieldX className="h-3 w-3" aria-hidden />
                                )}
                                {verification.authentic
                                  ? tx("evolve.promotions.authentic", "Ed25519 signature authentic")
                                  : tx("evolve.promotions.tampered", "certificate invalid or tampered")}
                              </span>
                            ) : null}
                            {canMerge ? (
                              <Button
                                type="button"
                                size="sm"
                                variant={
                                  confirmAction === `merge:${record.id}` ? "default" : "outline"
                                }
                                className="h-7 px-2 text-[11px]"
                                disabled={busyAction === `merge:${record.id}`}
                                onClick={() => {
                                  if (confirmAction !== `merge:${record.id}`) {
                                    setConfirmAction(`merge:${record.id}`);
                                    return;
                                  }
                                  if (!token || !projectPath) return;
                                  void runAction(
                                    `merge:${record.id}`,
                                    () => mergeEvolvePromotion(token, projectPath, record.id),
                                    tx("evolve.promotions.mergeDone", "Merged (fast-forward)."),
                                  );
                                }}
                              >
                                {busyAction === `merge:${record.id}` ? (
                                  <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                                ) : (
                                  <GitMerge className="mr-1 h-3 w-3" aria-hidden />
                                )}
                                {confirmAction === `merge:${record.id}`
                                  ? tx("evolve.promotions.confirm", "Confirm?")
                                  : tx("evolve.promotions.merge", "Merge")}
                              </Button>
                            ) : null}
                            {canPublish ? (
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                className="h-7 px-2 text-[11px]"
                                disabled={busyAction === `pr:${record.id}`}
                                onClick={() => {
                                  if (!token || !projectPath) return;
                                  void runAction(
                                    `pr:${record.id}`,
                                    () => publishEvolvePromotion(token, projectPath, record.id),
                                    tx(
                                      "evolve.promotions.prDone",
                                      "Branch pushed; the pull request link is below.",
                                    ),
                                  );
                                }}
                              >
                                {busyAction === `pr:${record.id}` ? (
                                  <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                                ) : (
                                  <GitPullRequest className="mr-1 h-3 w-3" aria-hidden />
                                )}
                                {record.pull_request
                                  ? tx("evolve.promotions.prAgain", "Push again")
                                  : tx("evolve.promotions.pr", "Open pull request")}
                              </Button>
                            ) : null}
                            {record.pull_request ? (
                              <a
                                href={record.pull_request}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex h-7 items-center gap-1 rounded-md border border-border/50 px-2 text-[11px] font-medium text-foreground transition-colors hover:bg-accent/40"
                              >
                                <ExternalLink className="h-3 w-3" aria-hidden />
                                {tx("evolve.promotions.prOpen", "View on the forge")}
                              </a>
                            ) : null}
                            {canRollback ? (
                              <Button
                                type="button"
                                size="sm"
                                variant={
                                  confirmAction === `rollback:${record.id}`
                                    ? "destructive"
                                    : "outline"
                                }
                                className="h-7 px-2 text-[11px]"
                                disabled={busyAction === `rollback:${record.id}`}
                                onClick={() => {
                                  if (confirmAction !== `rollback:${record.id}`) {
                                    setConfirmAction(`rollback:${record.id}`);
                                    return;
                                  }
                                  if (!token || !projectPath) return;
                                  void runAction(
                                    `rollback:${record.id}`,
                                    () => rollbackEvolvePromotion(token, projectPath, record.id),
                                    tx("evolve.promotions.rollbackDone", "Promotion rolled back."),
                                  );
                                }}
                              >
                                {busyAction === `rollback:${record.id}` ? (
                                  <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                                ) : (
                                  <Undo2 className="mr-1 h-3 w-3" aria-hidden />
                                )}
                                {confirmAction === `rollback:${record.id}`
                                  ? tx("evolve.promotions.confirm", "Confirm?")
                                  : tx("evolve.promotions.rollback", "Roll back")}
                              </Button>
                            ) : null}
                          </div>
                          {record.reasons.length ? (
                            <p className="mt-1 text-[11px] text-muted-foreground/80">
                              {record.reasons[record.reasons.length - 1]}
                            </p>
                          ) : null}
                          {record.diff ? (
                            <div className="mt-1.5">
                              <DiffDisclosure
                                diff={record.diff}
                                showLabel={diffShowLabel}
                                hideLabel={diffHideLabel}
                                copyLabel={diffCopyLabel}
                                copiedLabel={diffCopiedLabel}
                              />
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>
            </div>
          </>
        ) : null}

        {projectPath && !overview && loading ? (
          <div className="flex justify-center p-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
          </div>
        ) : null}
      </div>

      <Dialog open={docsOpen} onOpenChange={setDocsOpen}>
        <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BookOpen className="h-4 w-4" aria-hidden />
              {tx("evolve.docs.title", "Evolve Engine documentation")}
            </DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto pr-1 text-sm">
            {docsMarkdown === null ? (
              <div className="flex justify-center p-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
              </div>
            ) : (
              <MarkdownTextRenderer>{docsMarkdown}</MarkdownTextRenderer>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
