// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { CheckCircle2, Copy, FlaskConical, TerminalSquare, X, XCircle } from "lucide-react";
import { Component, Suspense, lazy, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  INSPECTOR_WIDTH_KEY,
  clampInspectorWidth,
  closeInspector,
  useInspectorTarget,
  type InspectorTarget,
} from "@/lib/activity-inspector";
import { tidyRunOutput } from "@/lib/ansi";
import { redactSecrets } from "@/lib/redact-secrets";
import { parseTestReport, type TestReport } from "@/lib/test-report";
import { cn } from "@/lib/utils";

const MonacoDiff = lazy(() => import("./MonacoDiff"));

function loadWidth(): number {
  try {
    return clampInspectorWidth(Number(window.localStorage.getItem(INSPECTOR_WIDTH_KEY) ?? 480));
  } catch {
    return 480;
  }
}

/** Right-hand panel: full command output with its test report, or a Monaco diff. */
export function ActivityInspector() {
  const target = useInspectorTarget();
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const [width, setWidth] = useState(loadWidth);
  const dragging = useRef(false);

  useEffect(() => {
    if (!target) return undefined;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") closeInspector();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [target]);

  const startResize = (ev: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    const startX = ev.clientX;
    const startW = width;
    const move = (e: PointerEvent) => {
      if (!dragging.current) return;
      const rtl = document.documentElement.dir === "rtl";
      setWidth(clampInspectorWidth(startW + (rtl ? e.clientX - startX : startX - e.clientX)));
    };
    const up = () => {
      dragging.current = false;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setWidth((w) => {
        try {
          window.localStorage.setItem(INSPECTOR_WIDTH_KEY, String(w));
        } catch {
          // storage unavailable
        }
        return w;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <AnimatePresence>
      {target ? (
        <motion.aside
          key="inspector"
          initial={reduceMotion ? false : { opacity: 0, x: 16 }}
          animate={{ opacity: 1, x: 0 }}
          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, x: 16 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
          className="relative order-last flex h-full min-h-0 shrink-0 flex-col border-s border-border/50 bg-background"
          style={{ width }}
          aria-label={t("inspector.title", { defaultValue: "Inspector" })}
          data-testid="activity-inspector"
          data-kind={target.kind}
        >
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label={t("inspector.resize", { defaultValue: "Resize the panel" })}
            onPointerDown={startResize}
            className="absolute inset-y-0 start-0 z-10 w-1.5 -translate-x-1/2 cursor-col-resize hover:bg-foreground/10"
          />
          <InspectorHeader target={target} />
          <div className="min-h-0 flex-1 overflow-auto">
            {target.kind === "command" ? <CommandBody target={target} /> : <DiffBody target={target} />}
          </div>
        </motion.aside>
      ) : null}
    </AnimatePresence>
  );
}

function InspectorHeader({ target }: { target: InspectorTarget }) {
  const { t } = useTranslation();
  const title = target.kind === "command" ? target.command : target.path;
  return (
    <header className="flex items-center gap-2 border-b border-border/50 px-3 py-2">
      {target.kind === "command" ? (
        <TerminalSquare className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      ) : null}
      <span dir="ltr" className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-foreground/90" title={title}>
        {title}
      </span>
      <button
        type="button"
        onClick={() => void navigator.clipboard?.writeText(target.kind === "command" ? redactSecrets(target.output) : target.modified)}
        className="rounded-md p-1 text-muted-foreground hover:bg-muted/60 hover:text-foreground"
        aria-label={t("inspector.copy", { defaultValue: "Copy" })}
        data-testid="activity-inspector-copy"
      >
        <Copy className="h-3.5 w-3.5" aria-hidden />
      </button>
      <button
        type="button"
        onClick={closeInspector}
        className="rounded-md p-1 text-muted-foreground hover:bg-muted/60 hover:text-foreground"
        aria-label={t("inspector.close", { defaultValue: "Close" })}
        data-testid="activity-inspector-close"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </header>
  );
}

function CommandBody({ target }: { target: Extract<InspectorTarget, { kind: "command" }> }) {
  const { t } = useTranslation();
  const output = useMemo(() => redactSecrets(tidyRunOutput(target.output).body), [target.output]);
  const report = useMemo(() => parseTestReport(target.output), [target.output]);
  const [tab, setTab] = useState<"tests" | "output">(report ? "tests" : "output");
  useEffect(() => setTab(report ? "tests" : "output"), [target.id, report]);
  return (
    <div className="flex min-h-full flex-col">
      {report ? (
        <div className="flex gap-1 px-3 pt-2" role="tablist">
          {([
            ["tests", t("inspector.testsTab", { defaultValue: "Tests" })],
            ["output", t("inspector.outputTab", { defaultValue: "Output" })],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              data-testid={`activity-inspector-tab-${id}`}
              className={cn(
                "rounded-md px-2 py-0.5 text-[12px]",
                tab === id ? "bg-foreground/[0.08] text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}
      {report && tab === "tests" ? (
        <TestReportView report={report} />
      ) : output ? (
        <pre dir="ltr" className="m-3 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[#101318] p-3 font-mono text-[12.5px] leading-[1.55] text-slate-200/90">
          {output}
        </pre>
      ) : (
        <p className="p-3 text-[13px] text-muted-foreground">
          {t("message.shellRunNoOutput", { defaultValue: "No output" })}
        </p>
      )}
    </div>
  );
}

export function TestReportView({ report }: { report: TestReport }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3 p-3" data-testid="activity-test-report">
      <div className="flex flex-wrap items-center gap-3 text-[13px]">
        <FlaskConical className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="text-muted-foreground">{report.runner}</span>
        <span className="inline-flex items-center gap-1 tabular-nums" data-testid="activity-test-passed">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
          {t("inspector.passed", { count: report.passed, defaultValue: "{{count}} passed" })}
        </span>
        {report.failed > 0 ? (
          <span className="inline-flex items-center gap-1 tabular-nums text-destructive" data-testid="activity-test-failed">
            <XCircle className="h-3.5 w-3.5" aria-hidden />
            {t("inspector.failed", { count: report.failed, defaultValue: "{{count}} failed" })}
          </span>
        ) : null}
        {report.skipped > 0 ? (
          <span className="tabular-nums text-muted-foreground">
            {t("inspector.skipped", { count: report.skipped, defaultValue: "{{count}} skipped" })}
          </span>
        ) : null}
      </div>
      {report.suites.length === 0 && report.failed > 0 ? (
        <p className="text-[12.5px] text-muted-foreground">
          {t("inspector.failuresInOutput", { defaultValue: "Failure details are in the Output tab." })}
        </p>
      ) : null}
      {report.suites.map((suite) => (
        <section key={suite.name} className="rounded-lg bg-muted/40">
          <h4 dir="ltr" className="flex items-center justify-between px-3 py-1.5 font-mono text-[12px] text-foreground/85">
            <span className="truncate">{suite.name}</span>
            <span className="tabular-nums text-destructive">{suite.failed}</span>
          </h4>
          <ul className="space-y-1 px-3 pb-2">
            {suite.failures.map((f) => (
              <li key={f.name} className="text-[12.5px]">
                <span dir="ltr" className="font-mono text-foreground/90">{f.name}</span>
                {f.message ? (
                  <span dir="ltr" className="block truncate font-mono text-[12px] text-muted-foreground" title={f.message}>
                    {f.message}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

class DiffFallbackBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function DiffBody({ target }: { target: Extract<InspectorTarget, { kind: "diff" }> }) {
  const { t } = useTranslation();
  const [sideBySide, setSideBySide] = useState(true);
  const dark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
  const fallback = (
    <pre dir="ltr" className="m-3 overflow-auto whitespace-pre font-mono text-[12px]" data-testid="activity-inspector-diff-fallback">
      {target.modified}
    </pre>
  );
  return (
    <div className="flex h-full min-h-[24rem] flex-col">
      <div className="flex justify-end px-3 py-1.5">
        <button
          type="button"
          aria-pressed={sideBySide}
          onClick={() => setSideBySide((v) => !v)}
          data-testid="activity-inspector-diff-mode"
          className="rounded-md px-2 py-0.5 text-[12px] text-muted-foreground hover:bg-muted/60 hover:text-foreground"
        >
          {sideBySide
            ? t("message.fileEditUnifiedView", { defaultValue: "Unified view" })
            : t("message.fileEditSplitView", { defaultValue: "Side by side" })}
        </button>
      </div>
      <div className="min-h-0 flex-1" data-testid="activity-inspector-diff">
        <DiffFallbackBoundary fallback={fallback}>
          <Suspense
            fallback={<p className="p-3 text-[13px] text-muted-foreground">{t("inspector.loadingDiff", { defaultValue: "Loading the diff..." })}</p>}
          >
            <MonacoDiff
              original={target.original}
              modified={target.modified}
              language={target.language}
              sideBySide={sideBySide}
              dark={dark}
            />
          </Suspense>
        </DiffFallbackBoundary>
      </div>
    </div>
  );
}
