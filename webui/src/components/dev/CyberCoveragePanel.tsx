// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * "Couverture Cyber": coverage matrix of the 302-control Navin Cyber AppSec
 * referential for the project's last security audit. Runs an audit, filters
 * the controls and exports the matrix (JSON / Markdown).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { saveTextFile } from "@/lib/save-blob";
import {
  CONTROL_STATUSES,
  categoryName,
  exportCoverageMatrix,
  exportFilename,
  fetchCoverageMatrix,
  filterControls,
  formatElapsed,
  formatPercent,
  groupByCategory,
  runCoverageAudit,
  type ControlStatus,
  type CoverageControl,
  type MatrixPayload,
} from "@/lib/cyber-coverage";

const STATUS_TONE: Record<ControlStatus, string> = {
  passed: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  failed: "bg-red-500/15 text-red-600 dark:text-red-400",
  inconclusive: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  blocked: "bg-orange-500/15 text-orange-600 dark:text-orange-400",
  not_tested: "bg-muted text-muted-foreground",
  not_applicable: "bg-muted/60 text-muted-foreground",
};

const STATUS_DEFAULTS: Record<ControlStatus, string> = {
  passed: "Passed",
  failed: "Failed",
  inconclusive: "Inconclusive",
  blocked: "Blocked",
  not_tested: "Not tested",
  not_applicable: "Not applicable",
};

export interface CyberCoveragePanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  token: string;
  /** Absolute path of the project audited. */
  root: string | null;
}

type LoadState = "loading" | "ready" | "error";

export function CyberCoveragePanel({ open, onOpenChange, token, root }: CyberCoveragePanelProps) {
  const { t, i18n } = useTranslation();
  const tx = useCallback(
    (key: string, defaultValue: string, values: Record<string, unknown> = {}) =>
      t(`dev.cyberCoverage.${key}`, { defaultValue, ...values }),
    [t],
  );
  const [state, setState] = useState<LoadState>("loading");
  const [matrix, setMatrix] = useState<MatrixPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runStart, setRunStart] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [scanners, setScanners] = useState(true);
  const [offline, setOffline] = useState(false);
  const [databaseEphemeral, setDatabaseEphemeral] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [statuses, setStatuses] = useState<Set<ControlStatus>>(new Set());
  const [openCategories, setOpenCategories] = useState<Set<string>>(new Set());
  const [openRows, setOpenRows] = useState<Set<string>>(new Set());
  const [exporting, setExporting] = useState<null | "json" | "md">(null);
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const load = useCallback(async () => {
    if (!root) return;
    setState("loading");
    setError(null);
    try {
      setMatrix(await fetchCoverageMatrix(tokenRef.current, root));
      setState("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState("error");
    }
  }, [root]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const run = async () => {
    if (!root || running) return;
    setRunning(true);
    const start = Date.now();
    setRunStart(start);
    setNow(start);
    setError(null);
    try {
      const next = await runCoverageAudit(tokenRef.current, root, { scanners, offline, baseUrl, databaseEphemeral });
      setMatrix(next ?? null);
      setState("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState("error");
    } finally {
      setRunning(false);
      setRunStart(null);
    }
  };

  const doExport = async (format: "json" | "md") => {
    if (!root || !matrix) return;
    setExporting(format);
    setError(null);
    try {
      const out = await exportCoverageMatrix(tokenRef.current, root, format);
      saveTextFile(
        exportFilename(out.filename, matrix.audit_id, format),
        out.content,
        out.mime || (format === "json" ? "application/json" : "text/markdown"),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(null);
    }
  };

  const filtered = useMemo(
    () => (matrix ? filterControls(matrix.controls, { query, category, statuses }) : []),
    [matrix, query, category, statuses],
  );
  const groups = useMemo(
    () =>
      matrix
        ? groupByCategory(matrix.categories, filtered).filter(
            (group) => group.controls.length > 0 || (!query && statuses.size === 0 && (!category || group.category.id === category)),
          )
        : [],
    [matrix, filtered, query, statuses, category],
  );

  const statusLabel = (status: ControlStatus) => tx(`status.${status}`, STATUS_DEFAULTS[status]);
  const toggle = <T,>(set: Set<T>, value: T): Set<T> => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  };
  const busy = running || exporting !== null;
  const lang = i18n?.language ?? "en";

  const runControls = (
    <div className="space-y-2 rounded-md border p-3" data-testid="cyber-coverage-run">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={scanners} disabled={running} onChange={(e) => setScanners(e.target.checked)} />
          {tx("scanners", "Run the scanners")}
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={offline} disabled={running} onChange={(e) => setOffline(e.target.checked)} />
          {tx("offline", "Offline (no network)")}
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={databaseEphemeral}
            disabled={running}
            onChange={(e) => setDatabaseEphemeral(e.target.checked)}
          />
          {tx("databaseEphemeral", "Test the SQL migrations in a throwaway local PostgreSQL")}
        </label>
      </div>
      <label className="block text-sm">
        <span className="mb-1 block text-xs text-muted-foreground">{tx("baseUrl", "Target URL for dynamic checks (optional)")}</span>
        <Input
          value={baseUrl}
          disabled={running}
          placeholder="https://staging.example.com"
          onChange={(e) => setBaseUrl(e.target.value)}
          aria-label={tx("baseUrl", "Target URL for dynamic checks (optional)")}
        />
      </label>
      <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        {tx("baseUrlWarning", "Dynamic checks only go to a target you are authorized to test, preferably staging.")}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => void run()} disabled={busy || !root} data-testid="cyber-coverage-run-button">
          {running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
          {tx("run", "Run the audit")}
        </Button>
        {running && runStart !== null ? (
          <span className="text-xs text-muted-foreground" role="status" data-testid="cyber-coverage-running">
            {tx("running", "Audit running ({{elapsed}}). It can take several minutes.", {
              elapsed: formatElapsed(now - runStart),
            })}
          </span>
        ) : null}
      </div>
    </div>
  );

  const renderControl = (control: CoverageControl) => {
    const expanded = openRows.has(control.id);
    const detailId = `cyber-control-${control.id}`;
    return (
      <li key={control.id} className="border-t first:border-t-0">
        <button
          type="button"
          className="flex w-full items-start gap-2 px-2 py-1.5 text-start text-sm hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-expanded={expanded}
          aria-controls={detailId}
          onClick={() => setOpenRows((rows) => toggle(rows, control.id))}
          data-testid={`cyber-control-${control.id}`}
        >
          {expanded ? <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> : <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />}
          <span className="w-20 shrink-0 font-mono text-xs">{control.id}</span>
          <span className="min-w-0 flex-1">{control.task}</span>
          <span className={cn("shrink-0 rounded px-1.5 py-0.5 text-xs", STATUS_TONE[control.status])}>
            {statusLabel(control.status)}
          </span>
          <span className="hidden w-40 shrink-0 truncate text-xs text-muted-foreground sm:block">
            {control.tools.map((tool) => `${tool.name}${tool.version ? ` ${tool.version}` : ""}`).join(", ")}
          </span>
        </button>
        {expanded ? (
          <dl id={detailId} className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 px-8 pb-2 text-xs">
            <dt className="text-muted-foreground">{tx("reason", "Reason")}</dt>
            <dd>{control.reason || "-"}</dd>
            <dt className="text-muted-foreground">{tx("evidence", "Evidence")}</dt>
            <dd className="break-all font-mono">{control.evidence_refs.length ? control.evidence_refs.join(", ") : "-"}</dd>
            <dt className="text-muted-foreground">{tx("findings", "Findings")}</dt>
            <dd>{control.findings_count}</dd>
            <dt className="text-muted-foreground">{tx("risk", "Risk")}</dt>
            <dd>{tx(`riskLevel.${control.risk}`, control.risk)}</dd>
            <dt className="text-muted-foreground">{tx("remediation", "Remediation")}</dt>
            <dd>{control.remediation || "-"}</dd>
            <dt className="text-muted-foreground">{tx("target", "Target")}</dt>
            <dd className="break-all">{control.target || "-"}</dd>
            <dt className="text-muted-foreground">{tx("environment", "Environment")}</dt>
            <dd>{control.environment || "-"}</dd>
            <dt className="text-muted-foreground">{tx("executedAt", "Executed at")}</dt>
            <dd>{control.executed_at ? new Date(control.executed_at).toLocaleString(lang) : "-"}</dd>
            <dt className="text-muted-foreground">{tx("capability", "Capability")}</dt>
            <dd>{tx(`capabilityLevel.${control.capability}`, control.capability)}</dd>
            <dt className="text-muted-foreground">{tx("manualReview", "Manual review")}</dt>
            <dd>{control.manual_review ? tx("yes", "Yes") : tx("no", "No")}</dd>
          </dl>
        ) : null}
      </li>
    );
  };

  const summary = matrix?.summary;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl" data-testid="cyber-coverage-panel">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" aria-hidden />
            {tx("title", "Cyber coverage: 302 controls")}
          </DialogTitle>
          <DialogDescription>
            {tx("description", "Coverage matrix of the Navin Cyber AppSec referential for the last security audit of this project.")}
          </DialogDescription>
        </DialogHeader>

        {!root ? (
          <p className="text-sm text-muted-foreground">{tx("noProject", "Open a project first.")}</p>
        ) : state === "loading" && !running ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status" data-testid="cyber-coverage-loading">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {tx("loading", "Loading the coverage matrix...")}
          </p>
        ) : state === "error" && !running ? (
          <div className="space-y-2" role="alert" data-testid="cyber-coverage-error">
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            <Button size="sm" variant="outline" onClick={() => void load()}>
              <RefreshCw className="h-4 w-4" aria-hidden />
              {tx("retry", "Retry")}
            </Button>
          </div>
        ) : !matrix ? (
          <div className="space-y-3" data-testid="cyber-coverage-empty">
            <p className="text-sm text-muted-foreground">
              {tx("empty", "No security audit is saved for this project yet. Run one to fill the 302-control matrix.")}
            </p>
            {runControls}
          </div>
        ) : (
          <div className="space-y-4" data-testid="cyber-coverage-data">
            <div className="flex flex-wrap items-end gap-6">
              <div>
                <div className="text-4xl font-semibold tabular-nums" data-testid="cyber-coverage-percent">
                  {formatPercent(summary?.verified_coverage, lang)}
                </div>
                <div className="text-xs text-muted-foreground">
                  {tx("formula", "Applicable controls tested with evidence / applicable controls ({{verified}} / {{applicable}})", {
                    verified: summary?.verified ?? 0,
                    applicable: summary?.applicable ?? 0,
                  })}
                </div>
              </div>
              <div className="text-xs text-muted-foreground">
                <div>
                  {tx("auditedAt", "Audit of {{date}}", {
                    date: matrix.audited_at ? new Date(matrix.audited_at).toLocaleString(lang) : "-",
                  })}
                </div>
                <div>
                  {tx("levels", "Levels reached: {{levels}}", {
                    levels: matrix.levels_reached.length ? matrix.levels_reached.join(", ") : "-",
                  })}
                </div>
              </div>
              {summary?.audit_complete ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400" data-testid="cyber-coverage-complete">
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
                  {tx("complete", "Complete audit")}
                </span>
              ) : null}
            </div>

            {!summary?.audit_complete ? (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs" role="alert" data-testid="cyber-coverage-incomplete">
                <p className="flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                  {tx("incomplete", "Audit not complete: {{n}} required control(s) not executed.", {
                    n: summary?.complete_blockers ?? matrix.complete_blockers.length,
                  })}
                </p>
                {matrix.complete_blockers.length ? (
                  <p className="mt-1 break-words font-mono">{matrix.complete_blockers.join(", ")}</p>
                ) : null}
              </div>
            ) : null}
            {matrix.warning ? <p className="text-xs text-amber-600 dark:text-amber-400">{matrix.warning}</p> : null}

            <div className="flex flex-wrap gap-2" role="group" aria-label={tx("statusFilter", "Filter by status")}>
              {CONTROL_STATUSES.map((status) => (
                <button
                  key={status}
                  type="button"
                  aria-pressed={statuses.has(status)}
                  onClick={() => setStatuses((set) => toggle(set, status))}
                  className={cn(
                    "rounded-md px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    STATUS_TONE[status],
                    statuses.has(status) && "ring-2 ring-primary",
                  )}
                  data-testid={`cyber-coverage-count-${status}`}
                >
                  {statusLabel(status)} <span className="font-semibold tabular-nums">{summary?.[status] ?? 0}</span>
                </button>
              ))}
            </div>

            {matrix.applicability.length ? (
              <div className="flex flex-wrap gap-1.5" aria-label={tx("surfaces", "Detected surfaces")}>
                {matrix.applicability.map((item) => (
                  <span
                    key={item.tag}
                    title={item.reason}
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-xs",
                      item.detected ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-400" : "text-muted-foreground",
                    )}
                  >
                    {item.tag}
                    {!item.detected && item.reason ? ` - ${item.reason}` : ""}
                  </span>
                ))}
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <Input
                className="h-8 max-w-xs"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={tx("search", "Search a control")}
                aria-label={tx("search", "Search a control")}
              />
              <select
                className="h-8 rounded-md border bg-background px-2 text-sm"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                aria-label={tx("category", "Category")}
              >
                <option value="">{tx("allCategories", "All categories")}</option>
                {[...matrix.categories].sort((a, b) => a.number - b.number).map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.number}. {categoryName(cat, lang)}
                  </option>
                ))}
              </select>
              <div className="ms-auto flex flex-wrap gap-2">
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void load()}>
                  <RefreshCw className="h-4 w-4" aria-hidden />
                  {tx("reload", "Reload")}
                </Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void doExport("json")}>
                  <Download className="h-4 w-4" aria-hidden />
                  {tx("exportJson", "Export JSON")}
                </Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void doExport("md")}>
                  <Download className="h-4 w-4" aria-hidden />
                  {tx("exportMd", "Export Markdown")}
                </Button>
              </div>
            </div>
            {error ? <p className="text-xs text-red-600 dark:text-red-400" role="alert">{error}</p> : null}

            {runControls}

            <div className="space-y-1">
              {groups.length === 0 ? (
                <p className="text-sm text-muted-foreground">{tx("noMatch", "No control matches these filters.")}</p>
              ) : null}
              {groups.map(({ category: cat, controls }) => {
                const expanded = openCategories.has(cat.id);
                const sectionId = `cyber-category-${cat.id}`;
                return (
                  <section key={cat.id} className="rounded-md border">
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 px-2 py-1.5 text-start text-sm font-medium hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      aria-expanded={expanded}
                      aria-controls={sectionId}
                      onClick={() => setOpenCategories((set) => toggle(set, cat.id))}
                      data-testid={`cyber-category-${cat.id}`}
                    >
                      {expanded ? <ChevronDown className="h-4 w-4 shrink-0" aria-hidden /> : <ChevronRight className="h-4 w-4 shrink-0" aria-hidden />}
                      <span className="min-w-0 flex-1 truncate">
                        {cat.number}. {categoryName(cat, lang)}
                      </span>
                      <span className="flex shrink-0 flex-wrap gap-1 text-xs font-normal">
                        {CONTROL_STATUSES.filter((status) => cat[status] > 0).map((status) => (
                          <span key={status} className={cn("rounded px-1", STATUS_TONE[status])} title={statusLabel(status)}>
                            {cat[status]}
                          </span>
                        ))}
                        <span className="text-muted-foreground">/ {cat.total}</span>
                      </span>
                    </button>
                    {expanded ? (
                      <ul id={sectionId}>{controls.map(renderControl)}</ul>
                    ) : null}
                  </section>
                );
              })}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default CyberCoveragePanel;
