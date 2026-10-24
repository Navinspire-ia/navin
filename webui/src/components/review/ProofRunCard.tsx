// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { ChevronRight, CircleAlert, CircleCheck, CircleX, FlaskConical, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ProveChange } from "@/hooks/useProveChange";
import type { FaultOutcome, ProofReport } from "@/lib/evolve-api";
import { formatActivityDuration } from "@/components/thread/activity/activityJournalModel";
import { cn } from "@/lib/utils";

const VERDICT_TONE: Record<ProofReport["verdict"], string> = {
  pass: "text-emerald-600 dark:text-emerald-400",
  weak: "text-amber-600 dark:text-amber-400",
  fail: "text-[hsl(var(--tone-error))]",
};

/** Requests, worst p95/p99 and best throughput across the faults that measured load. */
export function proofKpis(proof: ProofReport): { requests: number; p95: number | null; p99: number | null; rps: number | null } {
  let requests = 0;
  let p95: number | null = null;
  let p99: number | null = null;
  let rps: number | null = null;
  for (const fault of proof.faults) {
    for (const evidence of fault.evidence ?? []) {
      const req = /(\d+)\s*req\b/.exec(evidence);
      if (req) requests += Number(req[1]);
      const p95m = /p95\s*([\d.]+)\s*ms/.exec(evidence);
      if (p95m) p95 = Math.max(p95 ?? 0, Number(p95m[1]));
      const p99m = /p99\s*([\d.]+)\s*ms/.exec(evidence);
      if (p99m) p99 = Math.max(p99 ?? 0, Number(p99m[1]));
      const rpsm = /([\d.]+)\s*rps\b/.exec(evidence);
      if (rpsm) rps = Math.max(rps ?? 0, Number(rpsm[1]));
    }
  }
  return { requests, p95, p99, rps };
}

/**
 * "Prove this change" in the conversation, like a sub-agent: what the run is
 * doing while it goes, then the verdict, the score, each fault with its
 * numbers and the problems the diagnosis found.
 */
export function ProofRunCard({ prove, href }: { prove: ProveChange; href: string }) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const state = prove.state;
  const [now, setNow] = useState(() => Date.now());
  const running = state.phase === "starting" || state.phase === "started";
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [running]);
  if (state.phase === "idle") return null;

  const startedAt = state.phase === "started" || state.phase === "done" ? state.startedAt : undefined;
  const endedAt = state.phase === "done" ? state.endedAt : undefined;
  const elapsed = startedAt ? formatActivityDuration((endedAt ?? now) - startedAt, t) : "";
  const proof = state.phase === "done" ? state.proof : null;
  const diagnosis = state.phase === "done" ? state.diagnosis ?? null : null;
  const passed = proof ? proof.faults.filter((fault) => fault.verdict === "pass").length : 0;
  const kpis = proof ? proofKpis(proof) : null;

  const status = state.phase === "error"
    ? t("thread.proof.couldNotStart", { defaultValue: "Could not start" })
    : running
      ? t("thread.proof.running", { defaultValue: "Running" })
      : !proof
        ? t("thread.proof.finished", { defaultValue: "Finished" })
        : proof.verdict === "pass"
          ? t("thread.proof.passed", { defaultValue: "Passed" })
          : proof.verdict === "weak"
            ? t("thread.proof.weak", { defaultValue: "Weak" })
            : t("thread.proof.problems", { defaultValue: "Problems found" });

  return (
    <motion.section
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="mx-auto my-3 w-full max-w-[48rem] rounded-xl border border-border/60 bg-card/60 px-4 py-3"
      data-testid="proof-run-card"
      data-phase={state.phase}
      data-verdict={proof?.verdict ?? ""}
    >
      <header className="flex items-center gap-2.5">
        {running ? (
          <Loader2 aria-hidden className="h-[18px] w-[18px] shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none" />
        ) : proof?.verdict === "pass" ? (
          <CircleCheck aria-hidden className="h-[18px] w-[18px] shrink-0 text-[hsl(var(--tone-edit))]" />
        ) : state.phase === "error" || proof?.verdict === "fail" ? (
          <CircleX aria-hidden className="h-[18px] w-[18px] shrink-0 text-[hsl(var(--tone-error))]" />
        ) : (
          <FlaskConical aria-hidden className="h-[18px] w-[18px] shrink-0 text-muted-foreground" />
        )}
        <h3 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-foreground">
          {t("thread.proof.title", { defaultValue: "Robustness proof" })}
          {state.phase !== "error" && state.phase !== "starting" && state.job !== null
            ? <span className="ms-1.5 font-normal text-muted-foreground">#{state.job}</span>
            : null}
        </h3>
        <span className={cn("shrink-0 text-[13.5px] font-medium", proof ? VERDICT_TONE[proof.verdict] : "text-muted-foreground")}>
          {status}
        </span>
        {elapsed ? <span className="shrink-0 text-[13px] tabular-nums text-muted-foreground">{elapsed}</span> : null}
        {!running ? (
          <button
            type="button"
            onClick={() => prove.reset()}
            aria-label={t("thread.proof.dismiss", { defaultValue: "Dismiss" })}
            className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/[0.05] hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
      </header>

      {running ? (
        <p className="mt-2 ps-7 text-[13.5px] text-muted-foreground" data-testid="proof-run-progress">
          {state.phase === "started" && state.progress
            ? [state.progress.state, state.progress.detail].filter(Boolean).join(" · ")
            : t("thread.proof.queued", { defaultValue: "Preparing the isolated copy and starting the app…" })}
        </p>
      ) : null}

      {state.phase === "error" ? (
        <p className="mt-2 ps-7 text-[13.5px] text-muted-foreground">{state.message}</p>
      ) : null}

      {state.phase === "done" && !proof && state.detail ? (
        <p className="mt-2 ps-7 text-[13.5px] text-muted-foreground">{state.detail}</p>
      ) : null}

      {proof && kpis ? (
        <div className="mt-3 ps-7">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-5" data-testid="proof-run-kpis">
            <Kpi label={t("thread.proof.score", { defaultValue: "Score" })} value={`${Math.round(proof.robustness_score)}/100`} tone={VERDICT_TONE[proof.verdict]} />
            <Kpi label={t("thread.proof.faults", { defaultValue: "Scenarios" })} value={`${passed}/${proof.faults.length}`} />
            <Kpi label={t("thread.proof.requests", { defaultValue: "Requests" })} value={kpis.requests ? kpis.requests.toLocaleString() : "-"} />
            <Kpi label="p95 / p99" value={kpis.p95 !== null ? `${kpis.p95} / ${kpis.p99 ?? "-"} ms` : "-"} />
            <Kpi label={t("thread.proof.throughput", { defaultValue: "Throughput" })} value={kpis.rps !== null ? `${Math.round(kpis.rps)} rps` : "-"} />
          </dl>
          <ul className="mt-3 flex flex-col gap-0.5">
            {proof.faults.map((fault) => <FaultRow key={fault.fault} fault={fault} />)}
          </ul>
          {diagnosis?.findings.length ? (
            <div className="mt-3">
              <div className="text-[13.5px] font-semibold text-foreground">
                {t("thread.proof.findings", { count: diagnosis.findings.length, defaultValue: "{{count}} problems found" })}
              </div>
              <ul className="mt-1 flex flex-col gap-1">
                {diagnosis.findings.map((finding) => (
                  <li key={finding.id} className="flex items-baseline gap-2 text-[13.5px]">
                    <CircleAlert aria-hidden className="h-3.5 w-3.5 shrink-0 translate-y-0.5 text-[hsl(var(--tone-shell))]" />
                    <span className="min-w-0 flex-1">
                      <span className="font-medium text-foreground/90">{finding.title}</span>
                      <span className="text-muted-foreground"> · {finding.severity}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      {!running ? (
        <a
          href={href}
          className="mt-3 inline-flex items-center gap-1 ps-7 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
          data-testid="proof-run-open"
        >
          {t("thread.proof.open", { defaultValue: "Open in Evolve" })}
          <ChevronRight aria-hidden className="rtl:rotate-180 h-3.5 w-3.5" />
        </a>
      ) : null}
    </motion.section>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-[12px] text-muted-foreground">{label}</dt>
      <dd className={cn("truncate text-[15px] font-semibold tabular-nums text-foreground", tone)}>{value}</dd>
    </div>
  );
}

/** One fault scenario: verdict, what was injected, and what it measured. */
function FaultRow({ fault }: { fault: FaultOutcome }) {
  const [open, setOpen] = useState(false);
  const evidence = fault.evidence ?? [];
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-8 w-full min-w-0 items-center gap-2 rounded-md px-1 py-0.5 text-start transition-colors hover:bg-foreground/[0.04]"
        data-testid="proof-run-fault"
      >
        {fault.verdict === "pass" ? (
          <CircleCheck aria-hidden className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--tone-edit))]" />
        ) : fault.verdict === "weak" ? (
          <CircleAlert aria-hidden className="h-3.5 w-3.5 shrink-0 text-amber-500" />
        ) : (
          <CircleX aria-hidden className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--tone-error))]" />
        )}
        <span className="shrink-0 font-mono text-[13px] text-foreground/90">{fault.fault}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground">{fault.description}</span>
        <span className={cn("shrink-0 text-[12.5px] font-medium", VERDICT_TONE[fault.verdict])}>{fault.verdict}</span>
      </button>
      {open ? (
        <div className="ms-6 mb-1 border-s border-border/50 ps-3 text-[12.5px] leading-5 text-muted-foreground">
          {evidence.map((line, index) => <div key={index}>{line}</div>)}
          {fault.checks.map((check) => (
            <div key={check.name}>
              <span className={VERDICT_TONE[check.verdict]}>{check.verdict}</span> · {check.name}: {check.detail}
            </div>
          ))}
        </div>
      ) : null}
    </li>
  );
}
