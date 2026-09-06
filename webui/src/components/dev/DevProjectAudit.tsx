import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Boxes,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  Database,
  FileCode2,
  FileDown,
  FlaskConical,
  Gauge,
  GitBranch,
  GitCommitHorizontal,
  Info,
  Layers,
  LayoutDashboard,
  Loader2,
  Lock,
  LockOpen,
  Network,
  PlayCircle,
  RefreshCw,
  Send,
  Server,
  Share2,
  ShieldAlert,
  ShieldCheck,
  StickyNote,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { CodeBlock } from "@/components/CodeBlock";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  buildAuditReportHtml,
  printHtmlReport,
  REPORT_SECTIONS,
  type ReportSection,
} from "@/components/dev/projectAuditReport";
import { fetchProjectAudit } from "@/lib/api";
import type {
  AuditFinding,
  AuditFindingsSection,
  AuditNote,
  AuditSeverity,
  ProjectAuditPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useThemeValue } from "@/hooks/useTheme";
import { useClient } from "@/providers/ClientProvider";

type Section =
  | "overview"
  | "api"
  | "architecture"
  | "explorer"
  | "rbac"
  | "sql"
  | "infra"
  | "pipeline"
  | "security"
  | "performance"
  | "notes";

const SECTIONS: Array<{ id: Section; icon: LucideIcon; key: string; fallback: string }> = [
  { id: "overview", icon: LayoutDashboard, key: "dev.audit.nav.overview", fallback: "Overview" },
  { id: "api", icon: Boxes, key: "dev.audit.nav.api", fallback: "API Modules" },
  { id: "architecture", icon: Share2, key: "dev.audit.nav.architecture", fallback: "Architecture" },
  { id: "explorer", icon: PlayCircle, key: "dev.audit.nav.explorer", fallback: "API Explorer" },
  { id: "rbac", icon: ShieldCheck, key: "dev.audit.nav.rbac", fallback: "Permissions / RBAC" },
  { id: "sql", icon: Database, key: "dev.audit.nav.sql", fallback: "SQL Queries" },
  { id: "infra", icon: Server, key: "dev.audit.nav.infra", fallback: "Infrastructure" },
  { id: "pipeline", icon: Layers, key: "dev.audit.nav.pipeline", fallback: "Core & Pipeline" },
  { id: "security", icon: ShieldAlert, key: "dev.audit.nav.security", fallback: "Security" },
  { id: "performance", icon: Gauge, key: "dev.audit.nav.performance", fallback: "Performance" },
  { id: "notes", icon: StickyNote, key: "dev.audit.nav.notes", fallback: "Key Notes" },
];

const METHOD_COLORS: Record<string, string> = {
  GET: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  POST: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  PUT: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  PATCH: "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  DELETE: "bg-red-500/15 text-red-600 dark:text-red-400",
  ANY: "bg-muted text-muted-foreground",
};

const SEVERITY_STYLES: Record<AuditSeverity, { badge: string; dot: string }> = {
  critical: { badge: "bg-red-500/15 text-red-600 dark:text-red-400", dot: "bg-red-500" },
  high: { badge: "bg-orange-500/15 text-orange-600 dark:text-orange-400", dot: "bg-orange-500" },
  medium: { badge: "bg-amber-500/15 text-amber-600 dark:text-amber-400", dot: "bg-amber-500" },
  low: { badge: "bg-sky-500/15 text-sky-600 dark:text-sky-400", dot: "bg-sky-500" },
};

function scoreTone(score: number): string {
  if (score >= 80) return "text-emerald-500";
  if (score >= 60) return "text-amber-500";
  if (score >= 40) return "text-orange-500";
  return "text-red-500";
}

function scoreRingTone(score: number): string {
  if (score >= 80) return "stroke-emerald-500";
  if (score >= 60) return "stroke-amber-500";
  if (score >= 40) return "stroke-orange-500";
  return "stroke-red-500";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

// Reports are cached per project path so tab switches are instant; the
// refresh button and any project change trigger a fresh scan.
const auditCache = new Map<string, ProjectAuditPayload>();

function ScoreRing({ score, label }: { score: number; label: string }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className="flex items-center gap-3">
      <svg width="68" height="68" viewBox="0 0 68 68" className="-rotate-90">
        <circle
          cx="34"
          cy="34"
          r={radius}
          fill="none"
          strokeWidth="6"
          className="stroke-muted"
        />
        <circle
          cx="34"
          cy="34"
          r={radius}
          fill="none"
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - score / 100)}
          className={scoreRingTone(score)}
        />
      </svg>
      <div>
        <p className={cn("text-2xl font-bold leading-none", scoreTone(score))}>{score}</p>
        <p className="mt-1 text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      </div>
    </div>
  );
}

function KpiCard({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="rounded-2xl border border-border/55 bg-background/60 p-3.5">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="h-3.5 w-3.5" aria-hidden />
        <span className="truncate text-[11px] font-medium uppercase tracking-wide">{label}</span>
      </div>
      <p className={cn("mt-1.5 text-xl font-bold text-foreground", tone)}>{value}</p>
      {hint ? <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function SectionCard({
  title,
  children,
  actions,
}: {
  title: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-border/55 bg-background/60">
      <div className="flex items-center justify-between gap-2 border-b border-border/40 px-4 py-2.5">
        <h3 className="text-[13px] font-semibold text-foreground">{title}</h3>
        {actions}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function MethodBadge({ method }: { method: string }) {
  return (
    <span
      className={cn(
        "inline-flex w-14 shrink-0 items-center justify-center rounded-md px-1.5 py-0.5 font-mono text-[10.5px] font-bold",
        METHOD_COLORS[method] ?? METHOD_COLORS.ANY,
      )}
    >
      {method}
    </span>
  );
}

function FileLink({
  file,
  line,
  onOpenFile,
}: {
  file: string;
  line?: number;
  onOpenFile?: (relPath: string) => void;
}) {
  const label = line ? `${file}:${line}` : file;
  if (!onOpenFile) {
    return <span className="font-mono text-[11px] text-muted-foreground">{label}</span>;
  }
  return (
    <button
      type="button"
      onClick={() => onOpenFile(file)}
      className="inline-flex items-center gap-0.5 truncate font-mono text-[11px] text-primary hover:underline"
      title={label}
    >
      <span className="truncate">{label}</span>
      <ArrowUpRight className="h-3 w-3 shrink-0" aria-hidden />
    </button>
  );
}

function MermaidDiagram({ code }: { code: string }) {
  const isDark = useThemeValue() === "dark";
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const renderNonce = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const nonce = ++renderNonce.current;
    setError(null);
    void (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: isDark ? "dark" : "neutral",
          securityLevel: "strict",
          flowchart: { curve: "basis" },
        });
        const { svg: rendered } = await mermaid.render(`project-audit-arch-${nonce}`, code);
        if (!cancelled && renderNonce.current === nonce) setSvg(rendered);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, isDark]);

  if (error) {
    return <CodeBlock language="mermaid" code={code} />;
  }
  if (!svg) {
    return (
      <div className="flex items-center justify-center py-10 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      </div>
    );
  }
  return (
    <div
      className="overflow-x-auto [&_svg]:mx-auto [&_svg]:max-w-none"
      // Mermaid output is generated locally from directory names with
      // securityLevel "strict" (labels are HTML-escaped by the library).
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

function SeveritySummary({ summary }: { summary: Record<AuditSeverity, number> }) {
  const { t } = useTranslation();
  const labels: Record<AuditSeverity, string> = {
    critical: t("dev.audit.severity.critical", { defaultValue: "Critical" }),
    high: t("dev.audit.severity.high", { defaultValue: "High" }),
    medium: t("dev.audit.severity.medium", { defaultValue: "Medium" }),
    low: t("dev.audit.severity.low", { defaultValue: "Low" }),
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      {(Object.keys(labels) as AuditSeverity[]).map((level) => (
        <span
          key={level}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-semibold",
            SEVERITY_STYLES[level].badge,
          )}
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", SEVERITY_STYLES[level].dot)} />
          {summary[level] ?? 0} {labels[level]}
        </span>
      ))}
    </div>
  );
}

function FindingsList({
  section,
  emptyLabel,
  onOpenFile,
}: {
  section: AuditFindingsSection;
  emptyLabel: string;
  onOpenFile?: (relPath: string) => void;
}) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<AuditSeverity | "all">("all");
  const visible = useMemo(
    () =>
      filter === "all"
        ? section.findings
        : section.findings.filter((finding) => finding.severity === filter),
    [filter, section.findings],
  );
  const grouped = useMemo(() => {
    const byCategory = new Map<string, AuditFinding[]>();
    for (const finding of visible) {
      const bucket = byCategory.get(finding.category) ?? [];
      bucket.push(finding);
      byCategory.set(finding.category, bucket);
    }
    return [...byCategory.entries()];
  }, [visible]);

  if (!section.findings.length) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-4 py-3 text-[13px] text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
        {emptyLabel}
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {section.engine ? (
        <p className="text-[11.5px] text-muted-foreground">
          {t("dev.audit.engineBlurb", {
            defaultValue:
              "Analyse locale AST + heuristiques filtrées. Seules les alertes à confiance haute/moyenne baissent le score.",
          })}
          {typeof section.scored_findings === "number"
            ? ` (${section.scored_findings}/${section.findings.length})`
            : null}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-1.5">
        {(["all", "critical", "high", "medium", "low"] as const).map((level) => (
          <button
            key={level}
            type="button"
            onClick={() => setFilter(level)}
            className={cn(
              "rounded-full px-2.5 py-1 text-[11.5px] font-medium transition-colors",
              filter === level
                ? "bg-primary text-primary-foreground"
                : "bg-muted/60 text-muted-foreground hover:bg-muted",
            )}
          >
            {level === "all"
              ? t("dev.audit.filterAll", { defaultValue: "All" })
              : t(`dev.audit.severity.${level}`, { defaultValue: level })}
            {level !== "all" ? ` (${section.summary[level] ?? 0})` : ` (${section.findings.length})`}
          </button>
        ))}
      </div>
      {grouped.map(([category, findings]) => (
        <div key={category} className="rounded-2xl border border-border/55 bg-background/60">
          <p className="border-b border-border/40 px-4 py-2 text-[12px] font-semibold text-foreground">
            {category}
            <span className="ml-2 font-normal text-muted-foreground">({findings.length})</span>
          </p>
          <ul className="divide-y divide-border/35">
            {findings.map((finding, index) => (
              <li key={`${finding.file}:${finding.line}:${index}`} className="space-y-1.5 px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={cn(
                      "rounded-md px-1.5 py-0.5 text-[10.5px] font-bold uppercase",
                      SEVERITY_STYLES[finding.severity].badge,
                    )}
                  >
                    {t(`dev.audit.severity.${finding.severity}`, {
                      defaultValue: finding.severity,
                    })}
                  </span>
                  {finding.confidence ? (
                    <span
                      className={cn(
                        "rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium",
                        finding.confidence === "high"
                          ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400"
                          : finding.confidence === "medium"
                            ? "border-amber-500/40 text-amber-600 dark:text-amber-400"
                            : "border-border text-muted-foreground",
                      )}
                      title={t("dev.audit.confidenceHint", {
                        defaultValue:
                          "High = preuve AST / motif fort. Medium = heuristique utile. Low = indicatif, hors score.",
                      })}
                    >
                      {t(`dev.audit.confidence.${finding.confidence}`, {
                        defaultValue: finding.confidence,
                      })}
                    </span>
                  ) : null}
                  <span className="min-w-0 flex-1 text-[13px] font-medium text-foreground">
                    {finding.message}
                  </span>
                </div>
                <FileLink file={finding.file} line={finding.line} onOpenFile={onOpenFile} />
                {finding.snippet ? (
                  <pre className="overflow-x-auto rounded-lg bg-muted/50 px-3 py-1.5 font-mono text-[11.5px] text-foreground/80">
                    {finding.snippet}
                  </pre>
                ) : null}
                {finding.evidence ? (
                  <p className="text-[11.5px] text-muted-foreground/90">
                    {t("dev.audit.evidence", { defaultValue: "Preuve" })}: {finding.evidence}
                  </p>
                ) : null}
                <p className="flex items-start gap-1.5 text-[12px] text-muted-foreground">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                  {finding.recommendation}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

const NOTE_STYLES: Record<AuditNote["kind"], { icon: LucideIcon; className: string }> = {
  risk: { icon: AlertTriangle, className: "border-red-500/30 bg-red-500/5 text-red-600 dark:text-red-400" },
  strength: { icon: CheckCircle2, className: "border-emerald-500/30 bg-emerald-500/5 text-emerald-600 dark:text-emerald-400" },
  action: { icon: ClipboardList, className: "border-amber-500/30 bg-amber-500/5 text-amber-600 dark:text-amber-400" },
  info: { icon: Info, className: "border-sky-500/30 bg-sky-500/5 text-sky-600 dark:text-sky-400" },
};

type TryItResponse = {
  status: number;
  statusText: string;
  timeMs: number;
  body: string;
  contentType: string;
};

function ApiExplorer({ audit }: { audit: ProjectAuditPayload }) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const endpoints = useMemo(() => {
    const rows: Array<{ method: string; path: string; source: string; summary?: string }> = [];
    for (const spec of audit.openapi.specs) {
      for (const endpoint of spec.endpoints) {
        rows.push({ ...endpoint, source: spec.path });
      }
    }
    for (const module of audit.api.modules) {
      for (const endpoint of module.endpoints) {
        rows.push({ method: endpoint.method, path: endpoint.path, source: module.module });
      }
    }
    const seen = new Set<string>();
    return rows.filter((row) => {
      const key = `${row.method} ${row.path}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [audit]);

  const [search, setSearch] = useState("");
  const [baseUrl, setBaseUrl] = useState(() => window.location.origin);
  const [method, setMethod] = useState("GET");
  const [path, setPath] = useState(endpoints[0]?.path ?? "/");
  const [headersText, setHeadersText] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [sending, setSending] = useState(false);
  const [response, setResponse] = useState<TryItResponse | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return endpoints;
    return endpoints.filter(
      (endpoint) =>
        endpoint.path.toLowerCase().includes(needle)
        || endpoint.method.toLowerCase().includes(needle),
    );
  }, [endpoints, search]);

  const send = useCallback(async () => {
    setSending(true);
    setSendError(null);
    setResponse(null);
    const started = performance.now();
    try {
      const headers: Record<string, string> = {};
      for (const line of headersText.split("\n")) {
        const separator = line.indexOf(":");
        if (separator > 0) {
          headers[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
        }
      }
      const url = `${baseUrl.replace(/\/+$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
      const res = await fetch(url, {
        method,
        headers,
        body: ["GET", "HEAD"].includes(method) ? undefined : bodyText || undefined,
      });
      const text = await res.text();
      let pretty = text;
      const contentType = res.headers.get("content-type") ?? "";
      if (contentType.includes("json")) {
        try {
          pretty = JSON.stringify(JSON.parse(text), null, 2);
        } catch {
          pretty = text;
        }
      }
      setResponse({
        status: res.status,
        statusText: res.statusText,
        timeMs: Math.round(performance.now() - started),
        body: pretty.slice(0, 100_000),
        contentType,
      });
    } catch (err) {
      setSendError(
        err instanceof Error
          ? `${err.message} - ${t("dev.audit.explorer.corsHint", {
              defaultValue:
                "the target server must be reachable from this browser (check that it is running and allows CORS)",
            })}`
          : String(err),
      );
    } finally {
      setSending(false);
    }
  }, [baseUrl, bodyText, headersText, method, path, t]);

  return (
    <div className="grid min-h-0 gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
      <SectionCard title={tx("dev.audit.explorer.catalog", "Detected endpoints")}>
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={tx("dev.audit.explorer.search", "Filter endpoints…")}
          className="mb-2 h-8 rounded-lg text-[12px]"
        />
        <ul className="max-h-[26rem] space-y-1 overflow-y-auto pr-1">
          {filtered.map((endpoint) => (
            <li key={`${endpoint.method} ${endpoint.path}`}>
              <button
                type="button"
                onClick={() => {
                  setMethod(
                    ["GET", "POST", "PUT", "PATCH", "DELETE"].includes(endpoint.method)
                      ? endpoint.method
                      : "GET",
                  );
                  setPath(endpoint.path);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-muted/60",
                  path === endpoint.path && "bg-muted/70 ring-1 ring-border/60",
                )}
              >
                <MethodBadge method={endpoint.method} />
                <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-foreground/85">
                  {endpoint.path}
                </span>
              </button>
            </li>
          ))}
          {!filtered.length ? (
            <li className="px-2 py-3 text-[12px] text-muted-foreground">
              {tx("dev.audit.explorer.none", "No endpoint matches.")}
            </li>
          ) : null}
        </ul>
      </SectionCard>

      <SectionCard title={tx("dev.audit.explorer.tryIt", "Try it out")}>
        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={method}
              onChange={(event) => setMethod(event.target.value)}
              className="h-9 rounded-lg border border-border/55 bg-background px-2 font-mono text-[12px] font-semibold text-foreground"
            >
              {["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <Input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              className="h-9 w-56 rounded-lg font-mono text-[12px]"
              placeholder="http://127.0.0.1:8000"
              aria-label={tx("dev.audit.explorer.baseUrl", "Base URL")}
            />
            <Input
              value={path}
              onChange={(event) => setPath(event.target.value)}
              className="h-9 min-w-[10rem] flex-1 rounded-lg font-mono text-[12px]"
              placeholder="/api/…"
              aria-label={tx("dev.audit.explorer.path", "Path")}
            />
            <Button
              type="button"
              onClick={() => void send()}
              disabled={sending || !path.trim()}
              className="h-9 rounded-lg px-3 text-[12.5px]"
            >
              {sending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Send className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              )}
              {tx("dev.audit.explorer.send", "Send")}
            </Button>
          </div>
          <div className="grid gap-2.5 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {tx("dev.audit.explorer.headers", "Headers (one per line, Name: value)")}
              </span>
              <textarea
                value={headersText}
                onChange={(event) => setHeadersText(event.target.value)}
                rows={4}
                spellCheck={false}
                placeholder={"Content-Type: application/json\nAuthorization: Bearer …"}
                className="w-full rounded-lg border border-border/55 bg-background/80 px-2.5 py-2 font-mono text-[12px] text-foreground placeholder:text-muted-foreground/60"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {tx("dev.audit.explorer.body", "Body (POST / PUT / PATCH)")}
              </span>
              <textarea
                value={bodyText}
                onChange={(event) => setBodyText(event.target.value)}
                rows={4}
                spellCheck={false}
                placeholder='{"example": true}'
                className="w-full rounded-lg border border-border/55 bg-background/80 px-2.5 py-2 font-mono text-[12px] text-foreground placeholder:text-muted-foreground/60"
              />
            </label>
          </div>
          {sendError ? (
            <p className="rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
              {sendError}
            </p>
          ) : null}
          {response ? (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-[12px]">
                <span
                  className={cn(
                    "rounded-md px-2 py-0.5 font-mono font-bold",
                    response.status < 300
                      ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                      : response.status < 500
                        ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                        : "bg-red-500/15 text-red-600 dark:text-red-400",
                  )}
                >
                  {response.status} {response.statusText}
                </span>
                <span className="text-muted-foreground">{response.timeMs} ms</span>
                {response.contentType ? (
                  <span className="truncate font-mono text-[11px] text-muted-foreground">
                    {response.contentType}
                  </span>
                ) : null}
              </div>
              <CodeBlock
                language={response.contentType.includes("json") ? "json" : "text"}
                code={response.body || "(empty body)"}
                showLineNumbers={false}
              />
            </div>
          ) : null}
        </div>
      </SectionCard>
    </div>
  );
}

export default function DevProjectAudit({
  projectPath,
  onOpenFile,
}: {
  projectPath: string | null;
  onOpenFile?: (relPath: string) => void;
}) {
  const { token } = useClient();
  const { t, i18n } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const numberFormat = useMemo(
    () => new Intl.NumberFormat(i18n.language === "fr" ? "fr-FR" : "en-US"),
    [i18n.language],
  );
  const fmt = useCallback((value: number) => numberFormat.format(value), [numberFormat]);

  const [audit, setAudit] = useState<ProjectAuditPayload | null>(
    projectPath ? auditCache.get(projectPath) ?? null : null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState<Section>("overview");
  const [exporting, setExporting] = useState(false);

  const exportPdf = useCallback(
    async (audit: ProjectAuditPayload, sections: ReportSection[]) => {
      setExporting(true);
      try {
        // The on-screen diagram may be dark-themed; re-render a neutral one
        // so it stays readable on paper.
        let architectureSvg: string | null = null;
        if (sections.includes("architecture")) {
          try {
            const mermaid = (await import("mermaid")).default;
            mermaid.initialize({
              startOnLoad: false,
              theme: "neutral",
              securityLevel: "strict",
              flowchart: { curve: "basis" },
            });
            const { svg } = await mermaid.render(
              `audit-export-${Date.now()}`,
              audit.architecture.mermaid,
            );
            architectureSvg = svg;
          } catch {
            architectureSvg = null;
          }
        }
        printHtmlReport(
          buildAuditReportHtml(audit, sections, tx, {
            locale: i18n.language,
            architectureSvg,
          }),
        );
      } finally {
        setExporting(false);
      }
    },
    [i18n.language, tx],
  );

  const load = useCallback(
    async (force: boolean) => {
      if (!projectPath || !token) return;
      if (!force) {
        const cached = auditCache.get(projectPath);
        if (cached) {
          setAudit(cached);
          return;
        }
      }
      setLoading(true);
      setError(null);
      try {
        const payload = await fetchProjectAudit(token, projectPath);
        auditCache.set(projectPath, payload);
        setAudit(payload);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [projectPath, token],
  );

  useEffect(() => {
    setAudit(projectPath ? auditCache.get(projectPath) ?? null : null);
    setSection("overview");
    void load(false);
  }, [load, projectPath]);

  if (!projectPath) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
        <LayoutDashboard className="h-8 w-8 opacity-40" aria-hidden />
        <p className="text-[13px]">
          {tx("dev.audit.noProject", "Open a project folder to generate its 360° report.")}
        </p>
      </div>
    );
  }

  if (loading && !audit) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
        <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
        <p className="text-[13px]">
          {tx("dev.audit.scanning", "Scanning the project (code, API, security, performance)…")}
        </p>
      </div>
    );
  }

  if (error && !audit) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <AlertTriangle className="h-7 w-7 text-destructive" aria-hidden />
        <p className="max-w-md text-[13px] text-destructive">{error}</p>
        <Button type="button" variant="outline" onClick={() => void load(true)}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          {tx("dev.audit.retry", "Retry")}
        </Button>
      </div>
    );
  }

  if (!audit) return null;

  const { overview } = audit;
  const generatedAt = new Date(audit.generated_at);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Header */}
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border/55 bg-muted/10 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[15px] font-bold text-foreground">
              {tx("dev.audit.title", "360° Vision")} · {audit.project.name}
            </h2>
            <span className={cn("text-[13px] font-bold", scoreTone(overview.health_score))}>
              {overview.health_score}/100
            </span>
          </div>
          <p className="truncate font-mono text-[11px] text-muted-foreground">
            {audit.project.path}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
          <span>
            {tx("dev.audit.generatedAt", "Generated")}{" "}
            {generatedAt.toLocaleTimeString(i18n.language === "fr" ? "fr-FR" : undefined)}
            {" · "}
            {(audit.duration_ms / 1000).toFixed(1)} s
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={loading}
            onClick={() => void load(true)}
            className="h-7 rounded-lg px-2.5 text-[11.5px]"
          >
            {loading ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="mr-1 h-3 w-3" aria-hidden />
            )}
            {tx("dev.audit.refresh", "Refresh")}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={exporting}
                className="h-7 rounded-lg px-2.5 text-[11.5px]"
              >
                {exporting ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                ) : (
                  <FileDown className="mr-1 h-3 w-3" aria-hidden />
                )}
                {tx("dev.audit.export.button", "Export PDF")}
                <ChevronDown className="ml-1 h-3 w-3" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56 rounded-xl">
              <DropdownMenuItem
                onSelect={() => void exportPdf(audit, REPORT_SECTIONS)}
                className="rounded-lg text-[12.5px] font-medium"
              >
                <FileDown className="mr-2 h-3.5 w-3.5" aria-hidden />
                {tx("dev.audit.export.full", "Full 360° report")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {tx("dev.audit.export.bySection", "One section only")}
              </DropdownMenuLabel>
              {REPORT_SECTIONS.map((id) => {
                const entry = SECTIONS.find((candidate) => candidate.id === id);
                if (!entry) return null;
                const Icon = entry.icon;
                return (
                  <DropdownMenuItem
                    key={id}
                    onSelect={() => void exportPdf(audit, [id])}
                    className="rounded-lg text-[12.5px]"
                  >
                    <Icon className="mr-2 h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                    {tx(entry.key, entry.fallback)}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Section rail */}
        <nav className="w-48 shrink-0 space-y-0.5 overflow-y-auto border-r border-border/55 bg-muted/10 p-2">
          {SECTIONS.map((entry) => {
            const Icon = entry.icon;
            const badge =
              entry.id === "security"
                ? audit.security.summary.critical + audit.security.summary.high
                : entry.id === "performance"
                  ? audit.performance.summary.high + audit.performance.summary.medium
                  : entry.id === "sql"
                    ? (audit.sql.summary?.critical ?? 0) + (audit.sql.summary?.high ?? 0)
                    : entry.id === "api"
                      ? audit.api.total
                      : entry.id === "rbac"
                        ? audit.permissions.unprotected_count
                        : null;
            return (
              <button
                key={entry.id}
                type="button"
                onClick={() => setSection(entry.id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12.5px] font-medium transition-colors",
                  section === entry.id
                    ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{tx(entry.key, entry.fallback)}</span>
                {badge ? (
                  <span
                    className={cn(
                      "rounded-full px-1.5 py-px text-[10px] font-bold",
                      entry.id === "security" && badge > 0
                        ? "bg-red-500/15 text-red-600 dark:text-red-400"
                        : "bg-muted text-muted-foreground",
                    )}
                  >
                    {badge}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>

        {/* Section content */}
        <div className="min-w-0 flex-1 overflow-y-auto p-4">
          {section === "overview" ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-6 rounded-2xl border border-border/55 bg-background/60 p-4">
                <ScoreRing
                  score={overview.health_score}
                  label={tx("dev.audit.health", "Global health")}
                />
                <ScoreRing
                  score={overview.scores.security}
                  label={tx("dev.audit.nav.security", "Security")}
                />
                <ScoreRing
                  score={overview.scores.performance}
                  label={tx("dev.audit.nav.performance", "Performance")}
                />
                <ScoreRing
                  score={overview.scores.sql ?? 100}
                  label={tx("dev.audit.nav.sql", "SQL")}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap gap-1.5">
                    {overview.stack.map((item) => (
                      <span
                        key={item}
                        className="rounded-full bg-primary/10 px-2.5 py-1 text-[11.5px] font-semibold text-primary"
                      >
                        {item}
                      </span>
                    ))}
                  </div>
                  {overview.git.is_repo ? (
                    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <GitBranch className="h-3.5 w-3.5" aria-hidden />
                        {overview.git.branch}
                        {overview.git.dirty ? (
                          <span className="text-amber-500">
                            ({tx("dev.audit.dirty", "uncommitted changes")})
                          </span>
                        ) : null}
                      </span>
                      {overview.git.commit_count ? (
                        <span className="inline-flex items-center gap-1">
                          <GitCommitHorizontal className="h-3.5 w-3.5" aria-hidden />
                          {fmt(overview.git.commit_count)} commits
                        </span>
                      ) : null}
                      {overview.git.contributors?.length ? (
                        <span className="inline-flex items-center gap-1">
                          <Users className="h-3.5 w-3.5" aria-hidden />
                          {overview.git.contributors.length}{" "}
                          {tx("dev.audit.contributors", "contributors")}
                        </span>
                      ) : null}
                    </p>
                  ) : null}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <KpiCard
                  icon={FileCode2}
                  label={tx("dev.audit.kpi.files", "Files")}
                  value={fmt(overview.totals.files)}
                  hint={formatBytes(overview.totals.bytes)}
                />
                <KpiCard
                  icon={Activity}
                  label={tx("dev.audit.kpi.codeLines", "Lines of code")}
                  value={fmt(overview.totals.code_lines)}
                  hint={`${fmt(overview.totals.lines)} ${tx("dev.audit.kpi.totalLines", "lines total")}`}
                />
                <KpiCard
                  icon={Network}
                  label={tx("dev.audit.kpi.endpoints", "API endpoints")}
                  value={fmt(overview.api_endpoints)}
                  hint={audit.api.frameworks.join(", ")}
                />
                <KpiCard
                  icon={Database}
                  label={tx("dev.audit.kpi.sql", "SQL queries")}
                  value={fmt(overview.sql_queries)}
                  hint={
                    overview.sql_files
                      ? `${fmt(overview.sql_files)} ${tx("dev.audit.filesLabel", "files")}`
                      : undefined
                  }
                />
                <KpiCard
                  icon={FlaskConical}
                  label={tx("dev.audit.kpi.tests", "Test files")}
                  value={fmt(overview.tests.files)}
                />
                <KpiCard
                  icon={StickyNote}
                  label={tx("dev.audit.kpi.docs", "Doc files")}
                  value={fmt(overview.docs.files)}
                />
                <KpiCard
                  icon={ClipboardList}
                  label="TODO / FIXME"
                  value={fmt(
                    overview.todos.todo + overview.todos.fixme + overview.todos.hack,
                  )}
                  hint={`${overview.todos.todo} TODO · ${overview.todos.fixme} FIXME · ${overview.todos.hack} HACK`}
                />
                <KpiCard
                  icon={Boxes}
                  label={tx("dev.audit.kpi.deps", "Dependencies")}
                  value={fmt(
                    overview.dependencies.managers.reduce(
                      (acc, manager) => acc + manager.runtime + manager.dev,
                      0,
                    ),
                  )}
                  hint={overview.dependencies.lockfiles.join(", ")}
                />
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <SectionCard title={tx("dev.audit.languages", "Languages")}>
                  <ul className="space-y-2">
                    {overview.languages.map((language) => (
                      <li key={language.name}>
                        <div className="mb-0.5 flex items-center justify-between text-[12px]">
                          <span className="font-medium text-foreground">{language.name}</span>
                          <span className="text-muted-foreground">
                            {fmt(language.lines)} ({language.percent}%)
                          </span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-primary/70"
                            style={{ width: `${Math.max(language.percent, 1.5)}%` }}
                          />
                        </div>
                      </li>
                    ))}
                  </ul>
                </SectionCard>
                <div className="space-y-4">
                  {overview.git.last_commits?.length ? (
                    <SectionCard title={tx("dev.audit.recentCommits", "Recent commits")}>
                      <ul className="space-y-1.5">
                        {overview.git.last_commits.map((commit) => (
                          <li key={commit.hash} className="flex items-baseline gap-2 text-[12px]">
                            <span className="shrink-0 font-mono text-[11px] text-primary">
                              {commit.hash}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-foreground/85">
                              {commit.message}
                            </span>
                            <span className="shrink-0 text-[11px] text-muted-foreground">
                              {commit.date}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </SectionCard>
                  ) : null}
                  <SectionCard title={tx("dev.audit.largestFiles", "Largest source files")}>
                    <ul className="space-y-1.5">
                      {overview.largest_files.map((file) => (
                        <li key={file.path} className="flex items-center gap-2 text-[12px]">
                          <FileLink file={file.path} onOpenFile={onOpenFile} />
                          <span className="ml-auto shrink-0 text-muted-foreground">
                            {fmt(file.lines)} {tx("dev.audit.lines", "lines")}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </SectionCard>
                </div>
              </div>
            </div>
          ) : section === "api" ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <KpiCard
                  icon={Network}
                  label={tx("dev.audit.kpi.endpoints", "API endpoints")}
                  value={fmt(audit.api.total)}
                />
                <KpiCard
                  icon={Boxes}
                  label={tx("dev.audit.kpi.modules", "Modules")}
                  value={fmt(audit.api.modules.length)}
                />
                <KpiCard
                  icon={Lock}
                  label={tx("dev.audit.kpi.protected", "With auth guard")}
                  value={fmt(audit.permissions.protected_endpoints)}
                  tone="text-emerald-500"
                />
                <KpiCard
                  icon={LockOpen}
                  label={tx("dev.audit.kpi.unprotected", "No guard detected")}
                  value={fmt(audit.permissions.unprotected_count)}
                  tone={audit.permissions.unprotected_count ? "text-amber-500" : undefined}
                />
              </div>
              {audit.api.modules.map((module) => (
                <SectionCard
                  key={module.module}
                  title={module.module}
                  actions={
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[10.5px] font-semibold text-muted-foreground">
                      {module.framework} · {module.endpoints.length}
                    </span>
                  }
                >
                  <ul className="divide-y divide-border/35">
                    {module.endpoints.map((endpoint) => (
                      <li
                        key={`${endpoint.method} ${endpoint.path} ${endpoint.line}`}
                        className="flex items-center gap-2.5 py-1.5"
                      >
                        <MethodBadge method={endpoint.method} />
                        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground/90">
                          {endpoint.path}
                        </span>
                        {endpoint.auth ? (
                          <Lock
                            className="h-3.5 w-3.5 shrink-0 text-emerald-500"
                            aria-label={tx("dev.audit.authDetected", "Auth guard detected")}
                          />
                        ) : (
                          <LockOpen
                            className="h-3.5 w-3.5 shrink-0 text-amber-500/80"
                            aria-label={tx("dev.audit.noAuthDetected", "No auth guard detected")}
                          />
                        )}
                        <FileLink
                          file={module.module}
                          line={endpoint.line}
                          onOpenFile={onOpenFile}
                        />
                      </li>
                    ))}
                  </ul>
                </SectionCard>
              ))}
              {!audit.api.modules.length ? (
                <p className="text-[13px] text-muted-foreground">
                  {tx("dev.audit.noApi", "No HTTP endpoint detected in this project.")}
                </p>
              ) : null}
            </div>
          ) : section === "architecture" ? (
            <div className="space-y-4">
              <SectionCard title={tx("dev.audit.archDiagram", "System architecture (Mermaid)")}>
                <MermaidDiagram code={audit.architecture.mermaid} />
              </SectionCard>
              <SectionCard title={tx("dev.audit.archLayers", "Detected layers")}>
                <div className="grid gap-2 md:grid-cols-2">
                  {audit.architecture.layers.map((layer) => (
                    <div
                      key={layer.name}
                      className="rounded-xl border border-border/50 bg-muted/20 px-3 py-2.5"
                    >
                      <p className="text-[12.5px] font-semibold text-foreground">{layer.name}</p>
                      <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                        {layer.dirs.map((dir) => `${dir}/`).join("  ")}
                      </p>
                      <p className="mt-0.5 text-[11px] text-muted-foreground">
                        {fmt(layer.files)} {tx("dev.audit.filesLabel", "files")}
                      </p>
                    </div>
                  ))}
                </div>
              </SectionCard>
            </div>
          ) : section === "explorer" ? (
            <ApiExplorer audit={audit} />
          ) : section === "rbac" ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <KpiCard
                  icon={Lock}
                  label={tx("dev.audit.kpi.protected", "With auth guard")}
                  value={fmt(audit.permissions.protected_endpoints)}
                  tone="text-emerald-500"
                />
                <KpiCard
                  icon={LockOpen}
                  label={tx("dev.audit.kpi.unprotected", "No guard detected")}
                  value={fmt(audit.permissions.unprotected_count)}
                  tone={audit.permissions.unprotected_count ? "text-amber-500" : undefined}
                />
                <KpiCard
                  icon={ShieldCheck}
                  label={tx("dev.audit.kpi.coverage", "Auth coverage")}
                  value={`${audit.permissions.coverage_percent ?? 0}%`}
                  hint={
                    audit.permissions.module_auth_endpoints
                      ? `${fmt(audit.permissions.module_auth_endpoints)} ${tx("dev.audit.moduleAuth", "module-level")}`
                      : undefined
                  }
                />
                <KpiCard
                  icon={Users}
                  label={tx("dev.audit.kpi.roles", "Roles found")}
                  value={fmt(audit.permissions.roles.length)}
                />
              </div>
              {audit.permissions.roles.length ? (
                <SectionCard title={tx("dev.audit.rolesTitle", "Roles referenced in code")}>
                  <div className="flex flex-wrap gap-1.5">
                    {audit.permissions.roles.map((role) => (
                      <span
                        key={role.name}
                        className="rounded-full bg-primary/10 px-2.5 py-1 text-[11.5px] font-semibold text-primary"
                      >
                        {role.name} · {role.occurrences}
                      </span>
                    ))}
                  </div>
                </SectionCard>
              ) : null}
              {audit.permissions.unprotected_endpoints.length ? (
                <SectionCard
                  title={tx(
                    "dev.audit.unprotectedTitle",
                    "Endpoints without a detected guard (heuristic)",
                  )}
                >
                  <ul className="divide-y divide-border/35">
                    {audit.permissions.unprotected_endpoints.map((endpoint) => (
                      <li
                        key={`${endpoint.module}:${endpoint.line}:${endpoint.method}`}
                        className="flex items-center gap-2.5 py-1.5"
                      >
                        <MethodBadge method={endpoint.method} />
                        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground/90">
                          {endpoint.path}
                        </span>
                        <FileLink
                          file={endpoint.module}
                          line={endpoint.line}
                          onOpenFile={onOpenFile}
                        />
                      </li>
                    ))}
                  </ul>
                </SectionCard>
              ) : null}
              <SectionCard title={tx("dev.audit.mechanismsTitle", "Detected auth / permission code")}>
                {audit.permissions.mechanisms.length ? (
                  <ul className="divide-y divide-border/35">
                    {audit.permissions.mechanisms.slice(0, 120).map((mechanism, index) => (
                      <li key={`${mechanism.file}:${mechanism.line}:${index}`} className="py-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10.5px] font-semibold text-muted-foreground">
                            {mechanism.kind}
                          </span>
                          <span className="font-mono text-[12px] text-foreground/90">
                            {mechanism.name}
                          </span>
                          <span className="ml-auto">
                            <FileLink
                              file={mechanism.file}
                              line={mechanism.line}
                              onOpenFile={onOpenFile}
                            />
                          </span>
                        </div>
                        <pre className="mt-1 overflow-x-auto rounded-lg bg-muted/50 px-3 py-1 font-mono text-[11px] text-foreground/70">
                          {mechanism.snippet}
                        </pre>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[13px] text-muted-foreground">
                    {tx(
                      "dev.audit.noRbac",
                      "No authentication or RBAC mechanism detected in the source.",
                    )}
                  </p>
                )}
              </SectionCard>
            </div>
          ) : section === "sql" ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <KpiCard
                  icon={Database}
                  label={tx("dev.audit.kpi.sql", "SQL queries")}
                  value={fmt(audit.sql.total)}
                  hint={`${fmt(audit.sql.files_scanned ?? 0)} ${tx("dev.audit.filesLabel", "files")}`}
                />
                <KpiCard
                  icon={ShieldAlert}
                  label={tx("dev.audit.sqlScore", "SQL score")}
                  value={`${audit.sql.score ?? 100}`}
                  tone={scoreTone(audit.sql.score ?? 100)}
                />
                <KpiCard
                  icon={AlertTriangle}
                  label={tx("dev.audit.sqlRisky", "Risky queries")}
                  value={fmt(audit.sql.queries.filter((q) => q.risky).length)}
                  tone={
                    audit.sql.queries.some((q) => q.risky) ? "text-amber-500" : "text-emerald-500"
                  }
                />
                <KpiCard
                  icon={Layers}
                  label={tx("dev.audit.sqlKinds", "Statement kinds")}
                  value={fmt(Object.keys(audit.sql.by_kind).length)}
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {Object.entries(audit.sql.by_kind).map(([kind, count]) => (
                  <span
                    key={kind}
                    className="rounded-full bg-muted px-2.5 py-1 font-mono text-[11.5px] font-semibold text-muted-foreground"
                  >
                    {kind} · {count}
                  </span>
                ))}
                {audit.sql.by_source ? (
                  <span className="rounded-full bg-muted/60 px-2.5 py-1 text-[11px] text-muted-foreground">
                    {tx("dev.audit.sqlSources", "Sources")}:{" "}
                    {Object.entries(audit.sql.by_source)
                      .map(([src, count]) => `${src} ${count}`)
                      .join(" · ")}
                  </span>
                ) : null}
              </div>
              {audit.sql.findings?.length ? (
                <SectionCard title={tx("dev.audit.sqlFindings", "SQL security & performance")}>
                  <FindingsList
                    section={{
                      score: audit.sql.score ?? 100,
                      summary: audit.sql.summary ?? {
                        critical: 0,
                        high: 0,
                        medium: 0,
                        low: 0,
                      },
                      findings: audit.sql.findings,
                      scored_findings: audit.sql.scored_findings,
                      engine: audit.sql.engine,
                    }}
                    emptyLabel={tx(
                      "dev.audit.noSqlFindings",
                      "No SQL security or performance issue detected.",
                    )}
                    onOpenFile={onOpenFile}
                  />
                </SectionCard>
              ) : audit.sql.total ? (
                <div className="flex items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-4 py-3 text-[13px] text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
                  {tx(
                    "dev.audit.noSqlFindings",
                    "No SQL security or performance issue detected.",
                  )}
                </div>
              ) : null}
              <SectionCard title={tx("dev.audit.sqlCatalog", "Query catalog")}>
                <div className="space-y-3">
                  {!audit.sql.total ? (
                    <p className="text-[13px] text-muted-foreground">
                      {tx("dev.audit.noSql", "No SQL query detected in this project.")}
                    </p>
                  ) : null}
                  {audit.sql.queries.map((query, index) => (
                    <div
                      key={`${query.file}:${query.line}:${index}`}
                      className={cn(
                        "overflow-hidden rounded-2xl border",
                        query.risky ? "border-red-500/40" : "border-border/55",
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-2 border-b border-border/40 bg-muted/20 px-3 py-2">
                        <span className="rounded-md bg-indigo-500/15 px-1.5 py-0.5 font-mono text-[10.5px] font-bold text-indigo-500">
                          {query.kind}
                        </span>
                        {query.source ? (
                          <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            {query.source}
                          </span>
                        ) : null}
                        {query.tables?.length ? (
                          <span className="font-mono text-[10.5px] text-muted-foreground">
                            {query.tables.join(", ")}
                          </span>
                        ) : null}
                        <FileLink file={query.file} line={query.line} onOpenFile={onOpenFile} />
                        {query.risky ? (
                          <span className="ml-auto inline-flex items-center gap-1 text-[11.5px] font-semibold text-red-500">
                            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                            {query.risk_reason}
                          </span>
                        ) : null}
                      </div>
                      <CodeBlock language="sql" code={query.sql} chrome="none" />
                    </div>
                  ))}
                </div>
              </SectionCard>
            </div>
          ) : section === "infra" ? (
            <div className="space-y-4">
              {audit.infrastructure.summary ? (
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  <KpiCard
                    icon={Server}
                    label={tx("dev.audit.kpi.compose", "Compose services")}
                    value={fmt(audit.infrastructure.summary.compose_services)}
                  />
                  <KpiCard
                    icon={Boxes}
                    label="Dockerfiles"
                    value={fmt(audit.infrastructure.summary.dockerfiles)}
                  />
                  <KpiCard
                    icon={Layers}
                    label={tx("dev.audit.ciTitle", "Continuous integration")}
                    value={fmt(audit.infrastructure.summary.ci_workflows)}
                    tone={
                      audit.infrastructure.summary.has_ci
                        ? "text-emerald-500"
                        : "text-amber-500"
                    }
                  />
                  <KpiCard
                    icon={AlertTriangle}
                    label={tx("dev.audit.envRisk", "Env not ignored")}
                    value={fmt(audit.infrastructure.summary.env_not_gitignored.length)}
                    tone={
                      audit.infrastructure.summary.env_not_gitignored.length
                        ? "text-red-500"
                        : "text-emerald-500"
                    }
                  />
                </div>
              ) : null}
              {audit.infrastructure.docker_compose.map((compose) => (
                <SectionCard
                  key={compose.file}
                  title={`Docker Compose · ${compose.file}`}
                >
                  <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                    {compose.services.map((service) => (
                      <div
                        key={service.name}
                        className="rounded-xl border border-border/50 bg-muted/20 px-3 py-2.5"
                      >
                        <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-foreground">
                          <Server className="h-3.5 w-3.5 text-primary" aria-hidden />
                          {service.name}
                        </p>
                        <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                          {service.image}
                        </p>
                        {service.ports.length ? (
                          <p className="mt-1 text-[11px] text-muted-foreground">
                            {tx("dev.audit.ports", "Ports")}: {service.ports.join(", ")}
                          </p>
                        ) : null}
                        {service.depends_on.length ? (
                          <p className="text-[11px] text-muted-foreground">
                            {tx("dev.audit.dependsOn", "Depends on")}:{" "}
                            {service.depends_on.join(", ")}
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </SectionCard>
              ))}
              {audit.infrastructure.dockerfiles.length ? (
                <SectionCard title="Dockerfiles">
                  <ul className="space-y-2">
                    {audit.infrastructure.dockerfiles.map((dockerfile) => (
                      <li key={dockerfile.path} className="text-[12px]">
                        <FileLink file={dockerfile.path} onOpenFile={onOpenFile} />
                        <span className="ml-2 text-muted-foreground">
                          {dockerfile.base_images.join(" → ")}
                          {dockerfile.exposed_ports.length
                            ? ` · EXPOSE ${dockerfile.exposed_ports.join(", ")}`
                            : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                </SectionCard>
              ) : null}
              {audit.infrastructure.ci.length ? (
                <SectionCard title={tx("dev.audit.ciTitle", "Continuous integration")}>
                  <ul className="space-y-2">
                    {audit.infrastructure.ci.map((workflow) => (
                      <li key={workflow.path} className="flex flex-wrap items-center gap-2 text-[12px]">
                        <span className="font-semibold text-foreground">{workflow.name}</span>
                        <span className="text-muted-foreground">
                          {workflow.triggers.join(", ")}
                        </span>
                        {workflow.jobs.length ? (
                          <span className="font-mono text-[11px] text-muted-foreground">
                            jobs: {workflow.jobs.join(", ")}
                          </span>
                        ) : null}
                        <span className="ml-auto">
                          <FileLink file={workflow.path} onOpenFile={onOpenFile} />
                        </span>
                      </li>
                    ))}
                  </ul>
                </SectionCard>
              ) : null}
              {audit.infrastructure.makefile_targets.length ? (
                <SectionCard title={tx("dev.audit.makeTitle", "Makefile targets")}>
                  <div className="flex flex-wrap gap-1.5">
                    {audit.infrastructure.makefile_targets.map((target) => (
                      <span
                        key={target}
                        className="rounded-md bg-muted px-2 py-0.5 font-mono text-[11.5px] text-foreground/80"
                      >
                        make {target}
                      </span>
                    ))}
                  </div>
                </SectionCard>
              ) : null}
              {audit.infrastructure.env_files.length ? (
                <SectionCard title={tx("dev.audit.envTitle", "Environment files")}>
                  <ul className="space-y-2">
                    {audit.infrastructure.env_files.map((env) => (
                      <li key={env.path} className="text-[12px]">
                        <div className="flex items-center gap-2">
                          <FileLink file={env.path} onOpenFile={onOpenFile} />
                          {env.is_example ? (
                            <span className="rounded bg-muted px-1.5 py-px text-[10px] font-semibold text-muted-foreground">
                              example
                            </span>
                          ) : env.gitignored ? (
                            <span className="rounded bg-emerald-500/15 px-1.5 py-px text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">
                              gitignored
                            </span>
                          ) : (
                            <span className="rounded bg-red-500/15 px-1.5 py-px text-[10px] font-semibold text-red-600 dark:text-red-400">
                              {tx("dev.audit.notIgnored", "not gitignored")}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                          {env.keys.join(", ")}
                        </p>
                      </li>
                    ))}
                  </ul>
                </SectionCard>
              ) : null}
              {!audit.infrastructure.docker_compose.length
              && !audit.infrastructure.dockerfiles.length
              && !audit.infrastructure.ci.length
              && !audit.infrastructure.makefile_targets.length
              && !audit.infrastructure.env_files.length ? (
                <p className="text-[13px] text-muted-foreground">
                  {tx(
                    "dev.audit.noInfra",
                    "No infrastructure file detected (Docker, CI, Makefile, .env).",
                  )}
                </p>
              ) : null}
            </div>
          ) : section === "pipeline" ? (
            <div className="space-y-4">
              {audit.pipeline.summary ? (
                <div className="grid grid-cols-3 gap-3">
                  <KpiCard
                    icon={PlayCircle}
                    label={tx("dev.audit.entrypoints", "Entry points")}
                    value={fmt(audit.pipeline.summary.entrypoints)}
                  />
                  <KpiCard
                    icon={Layers}
                    label={tx("dev.audit.middleware", "Request pipeline / middleware")}
                    value={fmt(audit.pipeline.summary.middleware)}
                  />
                  <KpiCard
                    icon={Boxes}
                    label={tx("dev.audit.coreLayers", "Core layers")}
                    value={fmt(audit.pipeline.summary.core_layers)}
                  />
                </div>
              ) : null}
              <SectionCard title={tx("dev.audit.entrypoints", "Entry points")}>
                {audit.pipeline.entrypoints.length ? (
                  <ul className="space-y-1.5">
                    {audit.pipeline.entrypoints.map((entry) => (
                      <li key={entry.path} className="flex items-center gap-2 text-[12px]">
                        <PlayCircle className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
                        <FileLink file={entry.path} onOpenFile={onOpenFile} />
                        <span className="ml-auto text-muted-foreground">{entry.kind}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[13px] text-muted-foreground">
                    {tx("dev.audit.noEntrypoints", "No conventional entry point detected.")}
                  </p>
                )}
              </SectionCard>
              <SectionCard title={tx("dev.audit.coreLayers", "Core layers")}>
                <div className="grid gap-2 md:grid-cols-2">
                  {audit.pipeline.core_layers.map((layer) => (
                    <div
                      key={layer.path}
                      className="rounded-xl border border-border/50 bg-muted/20 px-3 py-2.5"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <FileLink file={layer.path} onOpenFile={onOpenFile} />
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          {fmt(layer.files)} {tx("dev.audit.filesLabel", "files")}
                        </span>
                      </div>
                      <p className="mt-1 text-[12px] text-foreground/80">{layer.description}</p>
                    </div>
                  ))}
                </div>
              </SectionCard>
              {audit.pipeline.middleware.length ? (
                <SectionCard
                  title={tx("dev.audit.middleware", "Request pipeline / middleware")}
                >
                  <ul className="divide-y divide-border/35">
                    {audit.pipeline.middleware.map((middleware, index) => (
                      <li
                        key={`${middleware.file}:${middleware.line}:${index}`}
                        className="flex items-center gap-2 py-1.5"
                      >
                        <Layers className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                        <code className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-foreground/85">
                          {middleware.snippet}
                        </code>
                        <FileLink
                          file={middleware.file}
                          line={middleware.line}
                          onOpenFile={onOpenFile}
                        />
                      </li>
                    ))}
                  </ul>
                </SectionCard>
              ) : null}
            </div>
          ) : section === "security" ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-6 rounded-2xl border border-border/55 bg-background/60 p-4">
                <ScoreRing
                  score={audit.security.score}
                  label={tx("dev.audit.securityScore", "Security score")}
                />
                <SeveritySummary summary={audit.security.summary} />
              </div>
              <FindingsList
                section={audit.security}
                emptyLabel={tx(
                  "dev.audit.noSecurityFindings",
                  "No security risk detected by static analysis.",
                )}
                onOpenFile={onOpenFile}
              />
            </div>
          ) : section === "performance" ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-6 rounded-2xl border border-border/55 bg-background/60 p-4">
                <ScoreRing
                  score={audit.performance.score}
                  label={tx("dev.audit.perfScore", "Performance score")}
                />
                <SeveritySummary summary={audit.performance.summary} />
              </div>
              <FindingsList
                section={audit.performance}
                emptyLabel={tx(
                  "dev.audit.noPerfFindings",
                  "No performance risk detected by static analysis.",
                )}
                onOpenFile={onOpenFile}
              />
            </div>
          ) : (
            <div className="space-y-3">
              {audit.notes.map((note, index) => {
                const style = NOTE_STYLES[note.kind];
                const NoteIcon = style.icon;
                return (
                  <div
                    key={index}
                    className={cn("rounded-2xl border px-4 py-3", style.className)}
                  >
                    <p className="flex items-center gap-2 text-[13px] font-semibold">
                      <NoteIcon className="h-4 w-4 shrink-0" aria-hidden />
                      {note.title}
                    </p>
                    <p className="mt-1 text-[12.5px] leading-relaxed text-foreground/80">
                      {note.body}
                    </p>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
