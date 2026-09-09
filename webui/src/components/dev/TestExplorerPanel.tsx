// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useMemo, useState } from "react";
import {
  Beaker,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Loader2,
  Play,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  fetchTestCollection,
  runExplorerTests,
  type CollectedTest,
  type TestCollectionPayload,
  type TestRunOutcome,
  type TestSuiteInfo,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type RunStatus = "running" | "passed" | "failed";

type Props = {
  sessionKey: string;
};

/** Group a suite's tests by file, preserving collection order. */
function groupByFile(tests: CollectedTest[]): Map<string, CollectedTest[]> {
  const groups = new Map<string, CollectedTest[]>();
  for (const test of tests) {
    const list = groups.get(test.file);
    if (list) list.push(test);
    else groups.set(test.file, [test]);
  }
  return groups;
}

function outcomeSummary(outcome: TestRunOutcome): string {
  if (!outcome.ran) return outcome.skipped_reason;
  const bits = [`${outcome.passed} ok`];
  if (outcome.failed) bits.push(`${outcome.failed} ko`);
  if (outcome.skipped) bits.push(`${outcome.skipped} -`);
  return `${bits.join(", ")} (${outcome.duration_ms} ms)`;
}

export default function TestExplorerPanel({ sessionKey }: Props) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [collection, setCollection] = useState<TestCollectionPayload | null>(null);
  const [collecting, setCollecting] = useState(false);
  const [collectError, setCollectError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [openFiles, setOpenFiles] = useState<Set<string>>(new Set());
  // One shared map: keys are "runner" (whole suite) or "runner::target".
  const [runStatus, setRunStatus] = useState<Record<string, RunStatus>>({});
  const [lastOutcome, setLastOutcome] = useState<TestRunOutcome | null>(null);

  const collect = useCallback(async () => {
    if (!token || collecting) return;
    setCollecting(true);
    setCollectError(null);
    try {
      setCollection(await fetchTestCollection(token, sessionKey));
    } catch (err) {
      setCollectError(err instanceof Error ? err.message : String(err));
    } finally {
      setCollecting(false);
    }
  }, [token, sessionKey, collecting]);

  const run = useCallback(
    async (runner: string, target?: string, statusKeys?: string[]) => {
      if (!token) return;
      const keys = statusKeys ?? [target ? `${runner}::${target}` : runner];
      setRunStatus((prev) => {
        const next = { ...prev };
        for (const key of keys) next[key] = "running";
        return next;
      });
      let ok = false;
      try {
        const payload = await runExplorerTests(token, sessionKey, runner, target);
        const outcome = payload.outcomes[0] ?? null;
        setLastOutcome(outcome);
        ok = outcome != null && outcome.ok;
      } catch (err) {
        setLastOutcome({
          runner,
          ran: false,
          ok: false,
          passed: 0,
          failed: 0,
          skipped: 0,
          total: 0,
          duration_ms: 0,
          exit_code: null,
          skipped_reason: err instanceof Error ? err.message : String(err),
          failures: [],
        });
      }
      setRunStatus((prev) => {
        const next = { ...prev };
        for (const key of keys) next[key] = ok ? "passed" : "failed";
        return next;
      });
    },
    [token, sessionKey],
  );

  const toggleFile = useCallback((file: string) => {
    setOpenFiles((prev) => {
      const next = new Set(prev);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  }, []);

  const visibleSuites = useMemo(() => {
    const suites = collection?.suites ?? [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return suites;
    return suites
      .map((suite) => ({
        ...suite,
        tests: suite.tests.filter((test) =>
          test.id.toLowerCase().includes(needle),
        ),
      }))
      .filter((suite) => suite.tests.length > 0 || suite.runner.includes(needle));
  }, [collection, filter]);

  const statusIcon = (status: RunStatus | undefined) => {
    if (status === "running")
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />;
    if (status === "passed")
      return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden />;
    if (status === "failed")
      return <XCircle className="h-3.5 w-3.5 text-red-500" aria-hidden />;
    return <CircleDashed className="h-3.5 w-3.5 text-muted-foreground/60" aria-hidden />;
  };

  const renderSuite = (suite: TestSuiteInfo) => {
    if (!suite.available && !suite.tests.length) {
      return (
        <div key={suite.runner} className="px-3 py-1.5 text-xs text-muted-foreground/70">
          <span className="font-medium">{suite.runner}</span>
          {" - "}
          {suite.reason || t("dev.tests.unavailable", "not available")}
        </div>
      );
    }
    const groups = groupByFile(suite.tests);
    const suiteStatus = runStatus[suite.runner];
    return (
      <div key={suite.runner} className="border-b border-border/40 last:border-b-0">
        <div className="flex items-center gap-2 px-3 py-1.5">
          {statusIcon(suiteStatus)}
          <span className="text-xs font-semibold">{suite.runner}</span>
          <span className="text-[10px] text-muted-foreground">
            {suite.tests.length > 0
              ? t("dev.tests.count", "{{count}} tests", { count: suite.tests.length })
              : suite.collect_supported
                ? t("dev.tests.none", "no tests collected")
                : t("dev.tests.collectUnsupported", "run as a whole suite")}
          </span>
          <div className="ml-auto">
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[11px]"
              disabled={suiteStatus === "running"}
              onClick={() => void run(suite.runner)}
            >
              <Play className="mr-1 h-3 w-3" aria-hidden />
              {t("dev.tests.runSuite", "Run suite")}
            </Button>
          </div>
        </div>
        {suite.error ? (
          <div className="px-3 pb-1.5 text-[11px] text-amber-600 dark:text-amber-400">
            {suite.error}
          </div>
        ) : null}
        {[...groups.entries()].map(([file, tests]) => {
          const open = openFiles.has(file);
          const fileKey = `${suite.runner}::${file}`;
          return (
            <div key={file}>
              <button
                type="button"
                className="flex w-full items-center gap-1.5 px-3 py-1 text-left text-[11px] hover:bg-accent/40"
                onClick={() => toggleFile(file)}
              >
                {open ? (
                  <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                ) : (
                  <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                )}
                {statusIcon(runStatus[fileKey])}
                <span className="truncate font-mono">{file}</span>
                <span className="text-muted-foreground">({tests.length})</span>
                <span
                  role="button"
                  tabIndex={-1}
                  className="ml-auto rounded p-0.5 text-muted-foreground hover:text-foreground"
                  title={t("dev.tests.runFile", "Run this file")}
                  onClick={(event) => {
                    event.stopPropagation();
                    void run(suite.runner, file, [fileKey]);
                  }}
                >
                  <Play className="h-3 w-3" aria-hidden />
                </span>
              </button>
              {open
                ? tests.map((test) => {
                    const testKey = `${suite.runner}::${test.id}`;
                    return (
                      <div
                        key={test.id}
                        className="group flex items-center gap-1.5 py-0.5 pl-9 pr-3 text-[11px] hover:bg-accent/30"
                      >
                        {statusIcon(runStatus[testKey])}
                        <span className="truncate" title={test.id}>
                          {test.suite ? `${test.suite} > ` : ""}
                          {test.name}
                        </span>
                        <button
                          type="button"
                          className="ml-auto rounded p-0.5 text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100"
                          title={t("dev.tests.runOne", "Run this test")}
                          onClick={() =>
                            void run(suite.runner, test.run_target, [testKey])
                          }
                        >
                          <Play className="h-3 w-3" aria-hidden />
                        </button>
                      </div>
                    );
                  })
                : null}
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border/60 px-3 py-2">
        <Beaker className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="text-sm font-semibold">
          {t("dev.tests.title", "Tests")}
        </span>
        <input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder={t("dev.tests.filter", "Filter tests...")}
          className="ml-2 h-6 min-w-0 flex-1 rounded border border-border/60 bg-transparent px-2 text-[11px] outline-none focus:border-border"
        />
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[11px]"
          disabled={collecting || !token}
          onClick={() => void collect()}
        >
          {collecting ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="mr-1 h-3 w-3" aria-hidden />
          )}
          {t("dev.tests.collect", "Collect")}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {collectError ? (
          <div className="px-3 py-2 text-xs text-red-500">{collectError}</div>
        ) : null}
        {collection == null && !collecting && !collectError ? (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground">
            {t(
              "dev.tests.empty",
              "Collect the project's tests to browse and run them here.",
            )}
          </div>
        ) : null}
        {collecting && collection == null ? (
          <div className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {t("dev.tests.collecting", "Collecting tests - this can take a while on large projects")}
          </div>
        ) : null}
        {visibleSuites.map(renderSuite)}
      </div>

      {lastOutcome ? (
        <div className="border-t border-border/60 px-3 py-2 text-[11px]">
          <div
            className={cn(
              "flex items-center gap-1.5 font-medium",
              lastOutcome.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-500",
            )}
          >
            {lastOutcome.ok ? (
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <XCircle className="h-3.5 w-3.5" aria-hidden />
            )}
            {lastOutcome.runner}: {outcomeSummary(lastOutcome)}
          </div>
          {lastOutcome.failures.slice(0, 10).map((failure) => (
            <div key={`${failure.file}:${failure.name}`} className="mt-1 pl-5">
              <div className="font-mono text-red-500/90">
                {failure.file}
                {failure.line ? `:${failure.line}` : ""} {failure.name}
              </div>
              {failure.message ? (
                <div className="whitespace-pre-wrap break-words text-muted-foreground">
                  {failure.message}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
