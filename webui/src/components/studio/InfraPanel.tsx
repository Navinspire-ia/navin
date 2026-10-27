import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCw,
  Search,
  Server,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  discoverInfra,
  fetchInfraAudit,
  fetchInfraGraph,
  fetchInfraOperation,
  fetchInfraRuns,
  fetchInfraState,
  searchInfraCatalog,
  updateInfraPolicy,
  type InfraAuditItem,
  type InfraCatalogItem,
  type InfraDiscoverResult,
  type InfraGraph,
  type InfraLevel,
  type InfraOperation,
  type InfraPolicy,
  type InfraRisk,
  type InfraRun,
  type InfraState,
  type InfraTier,
} from "@/lib/api";

type Tab = "overview" | "catalog" | "graph" | "audit";
type Tt = (key: string, fallback: string, vars?: Record<string, unknown>) => string;

const LEVELS: InfraLevel[] = ["observe", "prepare", "execute"];
const RISKS: InfraRisk[] = ["read", "plan", "change", "critical"];
const TIERS: InfraTier[] = ["known", "available", "authorized", "tested"];
const SCOPE_KEYS: (keyof InfraPolicy["scopes"])[] = [
  "aws_profiles",
  "kube_contexts",
  "namespaces",
  "gcp_projects",
  "azure_subscriptions",
];
const BUILD_SOURCES = [
  { id: "kubernetes", label: "Kubernetes" },
  { id: "aws", label: "AWS" },
  { id: "terraform", label: "Terraform" },
  { id: "docker", label: "Docker" },
  { id: "gcp", label: "Google Cloud" },
  { id: "azure", label: "Azure" },
  { id: "openstack", label: "OpenStack" },
  { id: "vsphere", label: "vSphere" },
  { id: "proxmox", label: "Proxmox" },
];

const RISK_TONE: Record<InfraRisk, string> = {
  read: "bg-foreground/[0.06] text-foreground/75",
  plan: "bg-[#2f7de1]/10 text-[#1d4ed8] dark:text-[#93c5fd]",
  change: "bg-[#d4892a]/14 text-[#92400e] dark:text-[#fcd34d]",
  critical: "bg-[#b91c1c]/12 text-[#b91c1c] dark:text-[#fca5a5]",
};

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function Chip({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium", className)}>
      {children}
    </span>
  );
}

function RiskBadge({ risk, tt }: { risk: InfraRisk; tt: Tt }) {
  return (
    <Chip className={RISK_TONE[risk] ?? RISK_TONE.read}>
      <span data-testid={`risk-${risk}`}>{tt(`infra.risk.${risk}`, risk)}</span>
    </Chip>
  );
}

function Loading({ tt }: { tt: Tt }) {
  return (
    <div className="flex items-center gap-2 py-3 text-[12.5px] text-muted-foreground">
      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      {tt("infra.loading", "Loading...")}
    </div>
  );
}

function ErrorBox({ message, onRetry, tt }: { message: string; onRetry?: () => void; tt: Tt }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-[#b91c1c]/30 bg-[#b91c1c]/[0.06] px-3 py-2 text-[12.5px] text-[#b91c1c] dark:text-[#fca5a5]">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{tt("infra.error", "Could not load infrastructure data")}</p>
        <p className="break-words opacity-90">{message}</p>
      </div>
      {onRetry ? (
        <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-[11.5px]" onClick={onRetry}>
          {tt("infra.retry", "Retry")}
        </Button>
      ) : null}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="py-2 text-[12.5px] text-muted-foreground">{children}</p>;
}

function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="space-y-1.5 rounded-xl border border-border/60 bg-background/80 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
        {right}
      </div>
      {children}
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-border/60 bg-muted/25 px-2.5 py-1.5 text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-primary/50";

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function Overview({ token, sessionKey, tt }: { token: string; sessionKey: string; tt: Tt }) {
  const [state, setState] = useState<InfraState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const [markers, setMarkers] = useState("");
  const [discovering, setDiscovering] = useState<string | null>(null);
  const [discoverResults, setDiscoverResults] = useState<InfraDiscoverResult[] | null>(null);
  const [discoverError, setDiscoverError] = useState<string | null>(null);

  const applyState = useCallback((next: InfraState) => {
    setState(next);
    const draft: Record<string, string> = {};
    for (const key of SCOPE_KEYS) draft[key] = (next.policy?.scopes?.[key] ?? []).join(", ");
    setScopes(draft);
    setMarkers((next.policy?.production_markers ?? []).join(", "));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      applyState(await fetchInfraState(token, sessionKey));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [token, sessionKey, applyState]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (fields: Parameters<typeof updateInfraPolicy>[2]) => {
    setSaving(true);
    setSaveError(null);
    try {
      applyState(await updateInfraPolicy(token, sessionKey, fields));
    } catch (err) {
      setSaveError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const discover = async (provider: string) => {
    setDiscovering(provider);
    setDiscoverError(null);
    try {
      const out = await discoverInfra(token, sessionKey, provider, false);
      setDiscoverResults(out.results ?? []);
      try {
        applyState(await fetchInfraState(token, sessionKey));
      } catch {
        // The discovery result is still shown; the counters refresh on retry.
      }
    } catch (err) {
      setDiscoverError(errorText(err));
    } finally {
      setDiscovering(null);
    }
  };

  if (loading && !state) return <Loading tt={tt} />;
  if (error && !state) return <ErrorBox message={error} onRetry={() => void load()} tt={tt} />;
  if (!state) return <Empty>{tt("infra.empty", "No data yet.")}</Empty>;

  const level = state.policy?.level ?? "observe";
  // Only providers the engine can discover here: a tool such as ssh or psql is
  // listed as available but has no Discover action.
  const availableProviders = state.discoverable
    ? [...state.discoverable]
    : Array.from(new Set((state.tools ?? []).filter((tool) => tool.available).map((tool) => tool.provider)));
  const ctx = state.contexts;
  const contextRows: { id: string; label: string; value: string; production: boolean }[] = ctx
    ? [
        { id: "kubernetes", label: "Kubernetes", value: ctx.kubernetes?.current ?? "", production: Boolean(ctx.kubernetes?.production) },
        { id: "aws", label: "AWS", value: ctx.aws?.profile ?? "", production: Boolean(ctx.aws?.production) },
        { id: "gcp", label: "GCP", value: ctx.gcp?.project ?? "", production: Boolean(ctx.gcp?.production) },
        { id: "terraform", label: "Terraform", value: ctx.terraform?.workspace ?? "", production: Boolean(ctx.terraform?.production) },
      ]
    : [];

  return (
    <div className="space-y-2.5">
      <Section title={tt("infra.level.title", "Autonomy level")}>
        <div role="radiogroup" className="inline-flex rounded-lg border border-border/60 bg-muted/25 p-0.5">
          {LEVELS.map((item) => (
            <button
              key={item}
              type="button"
              role="radio"
              aria-checked={level === item}
              disabled={saving}
              onClick={() => item !== level && void save({ level: item })}
              className={cn(
                "rounded-md px-3 py-1 text-[12px] font-medium transition-colors",
                level === item ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {tt(`infra.level.${item}`, item)}
            </button>
          ))}
        </div>
        <ul className="space-y-0.5 text-[12px] text-muted-foreground">
          <li><span className="font-medium text-foreground/85">{tt("infra.level.observe", "observe")}</span>: {tt("infra.level.observeHelp", "Read-only: inspect, list and describe, never change anything.")}</li>
          <li><span className="font-medium text-foreground/85">{tt("infra.level.prepare", "prepare")}</span>: {tt("infra.level.prepareHelp", "Plans and dry runs; changes are prepared for your approval.")}</li>
          <li><span className="font-medium text-foreground/85">{tt("infra.level.execute", "execute")}</span>: {tt("infra.level.executeHelp", "Applies authorized changes within the scopes, with verification and rollback.")}</li>
        </ul>
        <p className="text-[12px] font-medium text-[#92400e] dark:text-[#fcd34d]">
          {tt("infra.level.criticalNote", "Critical operations (production, deletions, IAM, sensitive network, data, costly) always ask you first")}
        </p>
        {saveError ? <ErrorBox message={saveError} tt={tt} /> : null}
      </Section>

      <Section title={tt("infra.scopes.title", "Scopes")}>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {SCOPE_KEYS.map((key) => (
            <label key={key} className="space-y-0.5 text-[11.5px] text-muted-foreground">
              <span>{tt(`infra.scopes.${key}`, key)}</span>
              <input
                className={inputClass}
                value={scopes[key] ?? ""}
                placeholder={tt("infra.scopes.placeholder", "Comma-separated, empty = none")}
                onChange={(event) => setScopes((cur) => ({ ...cur, [key]: event.target.value }))}
              />
            </label>
          ))}
          <label className="space-y-0.5 text-[11.5px] text-muted-foreground">
            <span>{tt("infra.scopes.production_markers", "Production markers")}</span>
            <input className={inputClass} value={markers} onChange={(event) => setMarkers(event.target.value)} />
          </label>
        </div>
        <Button
          type="button"
          size="sm"
          className="h-7 px-3 text-[12px]"
          disabled={saving}
          onClick={() => {
            const next: Partial<InfraPolicy["scopes"]> = {};
            for (const key of SCOPE_KEYS) next[key] = splitList(scopes[key] ?? "");
            void save({ scopes: next, production_markers: splitList(markers) });
          }}
        >
          {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden /> : null}
          {tt("infra.scopes.save", "Save")}
        </Button>
      </Section>

      <Section title={tt("infra.contexts.title", "Contexts")}>
        {contextRows.length ? (
          <ul className="space-y-0.5 text-[12.5px]">
            {contextRows.map((row) => (
              <li key={row.id} className="flex items-center gap-2">
                <span className="w-20 shrink-0 text-muted-foreground">{row.label}</span>
                <span className="min-w-0 truncate font-mono text-[12px]">{row.value || tt("infra.none", "none")}</span>
                {row.production ? <Chip className="bg-[#b91c1c] text-white">{tt("infra.production", "production")}</Chip> : null}
              </li>
            ))}
          </ul>
        ) : (
          <Empty>{tt("infra.contexts.empty", "No context detected.")}</Empty>
        )}
      </Section>

      {state.warnings?.length ? (
        <Section title={tt("infra.warnings", "Warnings")}>
          <ul className="space-y-0.5 text-[12.5px] text-[#92400e] dark:text-[#fcd34d]">
            {state.warnings.map((warning, index) => (
              <li key={index} className="flex gap-1.5"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />{warning}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title={tt("infra.catalog.counters", "Registry")}>
        <div className="grid grid-cols-4 gap-1.5">
          {TIERS.map((tier) => (
            <div key={tier} className="rounded-lg bg-muted/30 px-2 py-1.5 text-center">
              <p className="text-[15px] font-semibold tabular-nums" data-testid={`tier-${tier}`}>{state.catalog?.[tier] ?? 0}</p>
              <p className="text-[11px] text-muted-foreground">{tt(`infra.tier.${tier}`, tier)}</p>
            </div>
          ))}
        </div>
        {state.catalog?.providers?.length ? (
          <ul className="flex flex-wrap gap-1.5 text-[11.5px]">
            {state.catalog.providers.map((stat) => (
              <li key={stat.provider} className="rounded-full border border-border/60 px-2 py-0.5" title={`${stat.source} ${stat.version}`}>
                {stat.provider} <span className="tabular-nums text-muted-foreground">{stat.count}</span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>{tt("infra.catalog.noProviders", "Nothing discovered yet. Run a discovery.")}</Empty>
        )}
      </Section>

      <Section title={tt("infra.tools.title", "Tools")}>
        {state.tools?.length ? (
          <ul className="grid gap-x-3 gap-y-0.5 text-[12.5px] sm:grid-cols-2">
            {state.tools.map((tool) => (
              <li key={tool.name} className="flex items-center gap-2">
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", tool.available ? "bg-[#1f9d74]" : "bg-foreground/25")} aria-hidden />
                <span className="font-mono text-[12px]">{tool.name}</span>
                <span className="min-w-0 truncate text-muted-foreground">
                  {tool.available ? tool.version || tt("infra.tools.available", "available") : tt("infra.tools.missing", "missing")}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>{tt("infra.tools.empty", "No tool detected.")}</Empty>
        )}
      </Section>

      <Section title={tt("infra.discover.title", "Discovery")}>
        <div className="flex flex-wrap gap-1.5">
          {availableProviders.map((provider) => (
            <Button key={provider} type="button" variant="outline" size="sm" className="h-7 px-2.5 text-[12px]" disabled={discovering !== null} onClick={() => void discover(provider)}>
              {discovering === provider ? <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden /> : null}
              {provider}
            </Button>
          ))}
          <Button type="button" size="sm" className="h-7 px-2.5 text-[12px]" disabled={discovering !== null} onClick={() => void discover("all")}>
            {discovering === "all" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden /> : <RefreshCw className="mr-1 h-3 w-3" aria-hidden />}
            {tt("infra.discover.all", "Discover all")}
          </Button>
        </div>
        {discovering ? <p className="text-[12px] text-muted-foreground">{tt("infra.discover.running", "Discovery running, this can take a few minutes...")}</p> : null}
        {discoverError ? <ErrorBox message={discoverError} tt={tt} /> : null}
        {discoverResults ? (
          discoverResults.length ? (
            <ul className="space-y-0.5 text-[12.5px]">
              {discoverResults.map((res) => (
                <li key={res.provider}>
                  <span className="font-medium">{res.provider}</span>{" "}
                  <span className="text-muted-foreground">
                    {tt("infra.discover.result", "{{count}} operations, {{ms}} ms", { count: res.count, ms: res.duration_ms })}
                  </span>
                  {res.errors?.map((err, index) => (
                    <p key={index} className="text-[12px] text-[#b91c1c] dark:text-[#fca5a5]">{err}</p>
                  ))}
                </li>
              ))}
            </ul>
          ) : (
            <Empty>{tt("infra.discover.none", "Discovery returned no result.")}</Empty>
          )
        ) : null}
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog
// ---------------------------------------------------------------------------

function ListBlock({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <p className="text-[11.5px] font-medium text-muted-foreground">{title}</p>
      <ul className="list-disc ps-4 text-[12.5px]">
        {items.map((item, index) => (
          <li key={index} className="break-words font-mono text-[12px]">{item}</li>
        ))}
      </ul>
    </div>
  );
}

function OperationDetail({ token, sessionKey, id, onBack, tt }: { token: string; sessionKey: string; id: string; onBack: () => void; tt: Tt }) {
  const [op, setOp] = useState<InfraOperation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    setOp(null);
    try {
      setOp(await fetchInfraOperation(token, sessionKey, id));
    } catch (err) {
      setError(errorText(err));
    }
  }, [token, sessionKey, id]);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-2">
      <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-[12px]" onClick={onBack}>
        <ArrowLeft className="mr-1 h-3.5 w-3.5" aria-hidden />
        {tt("infra.catalog.back", "Back to results")}
      </Button>
      {error ? <ErrorBox message={error} onRetry={() => void load()} tt={tt} /> : null}
      {!op && !error ? <Loading tt={tt} /> : null}
      {op ? (
        <div className="space-y-2 rounded-xl border border-border/60 bg-background/80 px-3 py-2.5" data-testid="infra-operation">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-[13px] font-medium">{op.command}</p>
            <RiskBadge risk={op.risk} tt={tt} />
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12.5px]">
            <dt className="text-muted-foreground">{tt("infra.op.provider", "Provider")}</dt><dd>{op.provider} / {op.service}</dd>
            <dt className="text-muted-foreground">{tt("infra.op.version", "Version")}</dt><dd>{op.version || "-"}</dd>
            <dt className="text-muted-foreground">{tt("infra.op.endpoint", "Endpoint")}</dt><dd className="break-all font-mono text-[12px]">{op.endpoint || "-"}</dd>
            <dt className="text-muted-foreground">{tt("infra.op.source", "Source")}</dt><dd>{op.source || "-"}</dd>
            <dt className="text-muted-foreground">{tt("infra.op.effects", "Effects")}</dt><dd>{op.effects || "-"}</dd>
          </dl>
          <ListBlock title={tt("infra.op.required", "Required parameters")} items={op.required} />
          <ListBlock title={tt("infra.op.optional", "Optional parameters")} items={op.optional} />
          <ListBlock title={tt("infra.op.permissions", "Permissions")} items={op.permissions} />
          <ListBlock title={tt("infra.op.examples", "Examples")} items={op.examples} />
          <ListBlock title={tt("infra.op.verify", "Verification")} items={op.verify} />
          <ListBlock title={tt("infra.op.rollback", "Rollback")} items={op.rollback} />
          <ListBlock title={tt("infra.op.compat", "Compatibility")} items={op.compat} />
          {op.decision ? (
            <div className="rounded-lg bg-muted/30 px-2.5 py-1.5 text-[12.5px]">
              <p>
                {tt("infra.op.decision", "Decision")}:{" "}
                <span className="font-semibold">{tt(`infra.decision.${op.decision.action}`, op.decision.action)}</span>
              </p>
              {op.decision.reasons?.length ? (
                <ul className="list-disc ps-4 text-muted-foreground">
                  {op.decision.reasons.map((reason, index) => <li key={index}>{reason}</li>)}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Catalog({ token, sessionKey, tt }: { token: string; sessionKey: string; tt: Tt }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [provider, setProvider] = useState("");
  const [risk, setRisk] = useState<InfraRisk | "">("");
  const [tier, setTier] = useState<InfraTier | "">("");
  const [data, setData] = useState<{ total: number; items: InfraCatalogItem[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [providers, setProviders] = useState<string[]>([]);

  useEffect(() => {
    const handle = setTimeout(() => setDebounced(q.trim()), 300);
    return () => clearTimeout(handle);
  }, [q]);

  useEffect(() => {
    let alive = true;
    fetchInfraState(token, sessionKey)
      .then((state) => alive && setProviders((state.catalog?.providers ?? []).map((p) => p.provider)))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [token, sessionKey]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await searchInfraCatalog(token, sessionKey, { q: debounced, provider, risk, tier, limit: 100 }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [token, sessionKey, debounced, provider, risk, tier]);

  useEffect(() => {
    void load();
  }, [load]);

  if (selected) {
    return <OperationDetail token={token} sessionKey={sessionKey} id={selected} onBack={() => setSelected(null)} tt={tt} />;
  }

  const selectClass = "rounded-lg border border-border/60 bg-muted/25 px-2 py-1.5 text-[12px] outline-none focus:border-primary/50";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="relative min-w-[12rem] flex-1">
          <Search className="pointer-events-none absolute start-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <input
            className={cn(inputClass, "ps-7")}
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder={tt("infra.catalog.search", "Search operations (e.g. list pods, s3 bucket)")}
            aria-label={tt("infra.catalog.search", "Search operations (e.g. list pods, s3 bucket)")}
          />
        </div>
        <select className={selectClass} value={provider} onChange={(event) => setProvider(event.target.value)} aria-label={tt("infra.op.provider", "Provider")}>
          <option value="">{tt("infra.catalog.allProviders", "All providers")}</option>
          {providers.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select className={selectClass} value={risk} onChange={(event) => setRisk(event.target.value as InfraRisk | "")} aria-label={tt("infra.catalog.risk", "Risk")}>
          <option value="">{tt("infra.catalog.allRisks", "All risks")}</option>
          {RISKS.map((r) => <option key={r} value={r}>{tt(`infra.risk.${r}`, r)}</option>)}
        </select>
        <select className={selectClass} value={tier} onChange={(event) => setTier(event.target.value as InfraTier | "")} aria-label={tt("infra.catalog.tier", "Tier")}>
          <option value="">{tt("infra.catalog.allTiers", "All tiers")}</option>
          {TIERS.map((r) => <option key={r} value={r}>{tt(`infra.tier.${r}`, r)}</option>)}
        </select>
      </div>
      {error ? <ErrorBox message={error} onRetry={() => void load()} tt={tt} /> : null}
      {loading && !data ? <Loading tt={tt} /> : null}
      {data ? (
        data.items.length ? (
          <>
            <p className="text-[11.5px] text-muted-foreground">
              {tt("infra.catalog.total", "{{shown}} of {{total}} operations", { shown: data.items.length, total: data.total })}
              {loading ? <Loader2 className="ms-1 inline h-3 w-3 animate-spin" aria-hidden /> : null}
            </p>
            <ul className="divide-y divide-border/50 rounded-xl border border-border/60">
              {data.items.map((item) => (
                <li key={item.id}>
                  <button type="button" onClick={() => setSelected(item.id)} className="flex w-full flex-wrap items-center gap-1.5 px-3 py-1.5 text-start hover:bg-muted/40">
                    <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{item.command}</span>
                    <RiskBadge risk={item.risk} tt={tt} />
                    {TIERS.map((tierName) => (
                      <Chip key={tierName} className={item[tierName] ? "bg-[#1f9d74]/12 text-[#166534] dark:text-[#86efac]" : "bg-foreground/[0.04] text-muted-foreground/60 line-through"}>
                        {tt(`infra.tier.${tierName}`, tierName)}
                      </Chip>
                    ))}
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <Empty>{tt("infra.catalog.empty", "No operation matches. Try another search or run a discovery.")}</Empty>
        )
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Graph
// ---------------------------------------------------------------------------

function GraphTab({ token, sessionKey, tt }: { token: string; sessionKey: string; tt: Tt }) {
  const [graph, setGraph] = useState<InfraGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [node, setNode] = useState<string | null>(null);
  const [nodeGraph, setNodeGraph] = useState<InfraGraph | null>(null);
  const [nodeError, setNodeError] = useState<string | null>(null);

  const load = useCallback(async (build?: string) => {
    if (build) setBuilding(build);
    else setLoading(true);
    setError(null);
    try {
      setGraph(await fetchInfraGraph(token, sessionKey, build ? { build: [build] } : {}));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
      setBuilding(null);
    }
  }, [token, sessionKey]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!node) return;
    let alive = true;
    setNodeGraph(null);
    setNodeError(null);
    fetchInfraGraph(token, sessionKey, { node })
      .then((g) => alive && setNodeGraph(g))
      .catch((err) => alive && setNodeError(errorText(err)));
    return () => {
      alive = false;
    };
  }, [node, token, sessionKey]);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const nodes = graph?.nodes ?? [];
    return (needle ? nodes.filter((n) => `${n.id} ${n.name} ${n.kind}`.toLowerCase().includes(needle)) : nodes).slice(0, 50);
  }, [graph, query]);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[12px] text-muted-foreground">{tt("infra.graph.buildFrom", "Build from")}</span>
        {BUILD_SOURCES.map((src) => (
          <Button key={src.id} type="button" variant="outline" size="sm" className="h-7 px-2.5 text-[12px]" disabled={building !== null} onClick={() => void load(src.id)}>
            {building === src.id ? <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden /> : null}
            {src.label}
          </Button>
        ))}
      </div>
      {error ? <ErrorBox message={error} onRetry={() => void load()} tt={tt} /> : null}
      {loading && !graph ? <Loading tt={tt} /> : null}
      {graph ? (
        <>
          <Section title={tt("infra.graph.summary", "Summary")}>
            <p className="text-[12.5px]">
              {tt("infra.graph.counts", "{{nodes}} resources, {{edges}} relations", { nodes: graph.summary?.nodes ?? 0, edges: graph.summary?.edges ?? 0 })}
            </p>
            {Object.keys(graph.summary?.kinds ?? {}).length ? (
              <ul className="flex flex-wrap gap-1.5 text-[11.5px]">
                {Object.entries(graph.summary.kinds).map(([kind, count]) => (
                  <li key={kind} className="rounded-full border border-border/60 px-2 py-0.5">{kind} <span className="tabular-nums text-muted-foreground">{count}</span></li>
                ))}
              </ul>
            ) : (
              <Empty>{tt("infra.graph.empty", "The graph is empty. Build it from a source above.")}</Empty>
            )}
            {graph.errors?.map((err, index) => <p key={index} className="text-[12px] text-[#b91c1c] dark:text-[#fca5a5]">{err}</p>)}
          </Section>
          <input className={inputClass} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tt("infra.graph.search", "Search a resource")} />
          {matches.length ? (
            <ul className="max-h-40 overflow-y-auto rounded-xl border border-border/60 text-[12.5px]">
              {matches.map((n) => (
                <li key={n.id}>
                  <button type="button" onClick={() => setNode(n.id)} className={cn("flex w-full items-center gap-2 px-3 py-1 text-start hover:bg-muted/40", node === n.id && "bg-muted/50")}>
                    <span className="text-muted-foreground">{n.kind}</span>
                    <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{n.name || n.id}</span>
                    <span className="text-[11px] text-muted-foreground">{n.provider}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : graph.nodes?.length ? (
            <Empty>{tt("infra.graph.noMatch", "No resource matches.")}</Empty>
          ) : null}
          {node ? (
            <Section title={node}>
              {nodeError ? <ErrorBox message={nodeError} tt={tt} /> : null}
              {!nodeGraph && !nodeError ? <Loading tt={tt} /> : null}
              {nodeGraph ? (
                <>
                  <p className="text-[11.5px] font-medium text-muted-foreground">{tt("infra.graph.impact", "Impact (what depends on it)")}</p>
                  {nodeGraph.impact?.length ? (
                    <ul className="list-disc ps-4 font-mono text-[12px]">{nodeGraph.impact.map((id) => <li key={id}>{id}</li>)}</ul>
                  ) : (
                    <Empty>{tt("infra.graph.noImpact", "Nothing depends on this resource.")}</Empty>
                  )}
                  <p className="text-[11.5px] font-medium text-muted-foreground">{tt("infra.graph.edges", "Relations")}</p>
                  {nodeGraph.edges?.length ? (
                    <ul className="font-mono text-[12px]">
                      {nodeGraph.edges.map((edge, index) => <li key={index}>{edge.src} <span className="text-muted-foreground">{edge.rel}</span> {edge.dst}</li>)}
                    </ul>
                  ) : (
                    <Empty>{tt("infra.graph.noEdges", "No relation.")}</Empty>
                  )}
                </>
              ) : null}
            </Section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit and runs
// ---------------------------------------------------------------------------

function boolMark(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value ? "yes" : "no";
}

function AuditTab({ token, sessionKey, tt }: { token: string; sessionKey: string; tt: Tt }) {
  const [audit, setAudit] = useState<InfraAuditItem[] | null>(null);
  const [runs, setRuns] = useState<InfraRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [a, r] = await Promise.all([fetchInfraAudit(token, sessionKey, 100), fetchInfraRuns(token, sessionKey)]);
      setAudit(a.items ?? []);
      setRuns(r.items ?? []);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [token, sessionKey]);

  useEffect(() => {
    void load();
  }, [load]);

  const yesNo = (value: boolean | null | undefined) => {
    const mark = boolMark(value);
    return mark === "-" ? mark : tt(`infra.${mark}`, mark);
  };

  return (
    <div className="space-y-2.5">
      <div className="flex justify-end">
        <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-[12px]" disabled={loading} onClick={() => void load()}>
          <RefreshCw className={cn("mr-1 h-3 w-3", loading && "animate-spin")} aria-hidden />
          {tt("infra.refresh", "Refresh")}
        </Button>
      </div>
      {error ? <ErrorBox message={error} onRetry={() => void load()} tt={tt} /> : null}
      {loading && !audit ? <Loading tt={tt} /> : null}
      {audit ? (
        <Section title={tt("infra.audit.title", "Audit")}>
          {audit.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead className="text-start text-muted-foreground">
                  <tr>
                    <th className="px-1 py-0.5 text-start font-medium">{tt("infra.audit.time", "Time")}</th>
                    <th className="px-1 py-0.5 text-start font-medium">{tt("infra.audit.command", "Command")}</th>
                    <th className="px-1 py-0.5 text-start font-medium">{tt("infra.catalog.risk", "Risk")}</th>
                    <th className="px-1 py-0.5 text-start font-medium">{tt("infra.op.decision", "Decision")}</th>
                    <th className="px-1 py-0.5 text-start font-medium">{tt("infra.audit.ok", "OK")}</th>
                    <th className="px-1 py-0.5 text-start font-medium">{tt("infra.audit.verified", "Verified")}</th>
                    <th className="px-1 py-0.5 text-start font-medium">{tt("infra.audit.duration", "Duration")}</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((row, index) => (
                    <tr key={`${row.ts}-${index}`} className="border-t border-border/40">
                      <td className="whitespace-nowrap px-1 py-0.5 text-muted-foreground">{row.ts}</td>
                      <td className="max-w-[18rem] truncate px-1 py-0.5 font-mono" title={row.command}>{row.command}</td>
                      <td className="px-1 py-0.5"><RiskBadge risk={row.risk} tt={tt} /></td>
                      <td className="px-1 py-0.5">{row.decision}</td>
                      <td className={cn("px-1 py-0.5", row.ok ? "text-[#1f9d74]" : "text-[#b91c1c]")}>{yesNo(row.ok)}</td>
                      <td className="px-1 py-0.5">{yesNo(row.verified)}</td>
                      <td className="whitespace-nowrap px-1 py-0.5 tabular-nums">{row.duration_ms} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>{tt("infra.audit.empty", "No infrastructure command recorded yet.")}</Empty>
          )}
        </Section>
      ) : null}
      {runs ? (
        <Section title={tt("infra.runs.title", "Runs")}>
          {runs.length ? (
            <ul className="space-y-2">
              {runs.map((run) => (
                <li key={run.id} className="rounded-lg bg-muted/25 px-2.5 py-1.5">
                  <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                    <span className="font-medium">{run.goal || run.id}</span>
                    <Chip className="bg-foreground/[0.06]">{run.status}</Chip>
                    <span className="text-[11px] text-muted-foreground">{run.created_at}</span>
                  </div>
                  {run.steps?.length ? (
                    <ol className="mt-1 space-y-0.5 text-[12px]">
                      {run.steps.map((step) => (
                        <li key={step.id} className="flex flex-wrap items-center gap-1.5">
                          <span className="text-muted-foreground">{step.status}</span>
                          <span>{step.title}</span>
                          {step.command ? <span className="font-mono text-[11.5px] text-muted-foreground">{step.command}</span> : null}
                          <RiskBadge risk={step.risk} tt={tt} />
                          <span className="text-[11px] text-muted-foreground">{step.decision}{step.owner ? ` / ${step.owner}` : ""}</span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <Empty>{tt("infra.runs.noSteps", "No step.")}</Empty>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <Empty>{tt("infra.runs.empty", "No run yet.")}</Empty>
          )}
        </Section>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

/** Infrastructure engine panel shown above the ops cards. Collapsed by default. */
export function InfraPanel({ token, sessionKey, defaultOpen = false }: { token: string; sessionKey: string; defaultOpen?: boolean }) {
  const { t } = useTranslation();
  const tt = useCallback<Tt>((key, fallback, vars) => String(t(key, { defaultValue: fallback, ...(vars ?? {}) })), [t]);
  const [open, setOpen] = useState(defaultOpen);
  const [tab, setTab] = useState<Tab>("overview");
  const tabs: { id: Tab; label: string }[] = [
    { id: "overview", label: tt("infra.tab.overview", "Overview") },
    { id: "catalog", label: tt("infra.tab.catalog", "Catalog") },
    { id: "graph", label: tt("infra.tab.graph", "Graph") },
    { id: "audit", label: tt("infra.tab.audit", "Audit and runs") },
  ];

  return (
    <div className="shrink-0 border-b border-border/55 px-5 py-2">
      <button
        type="button"
        onClick={() => setOpen((cur) => !cur)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-start text-[13px] font-medium text-foreground"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden />}
        <Server className="h-3.5 w-3.5 text-foreground/70" aria-hidden />
        {tt("infra.title", "Infrastructure")}
        <span className="text-[12px] font-normal text-muted-foreground">{tt("infra.subtitle", "Registry, policy, resource graph and audit")}</span>
      </button>
      {open ? (
        <div className="mt-2 max-h-[55vh] space-y-2 overflow-y-auto pb-1">
          <div role="tablist" className="flex flex-wrap gap-1.5">
            {tabs.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                onClick={() => setTab(item.id)}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-[11.5px] font-medium transition-colors",
                  tab === item.id
                    ? "border-primary/50 bg-primary/10 text-foreground"
                    : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          {tab === "overview" ? <Overview token={token} sessionKey={sessionKey} tt={tt} /> : null}
          {tab === "catalog" ? <Catalog token={token} sessionKey={sessionKey} tt={tt} /> : null}
          {tab === "graph" ? <GraphTab token={token} sessionKey={sessionKey} tt={tt} /> : null}
          {tab === "audit" ? <AuditTab token={token} sessionKey={sessionKey} tt={tt} /> : null}
        </div>
      ) : null}
    </div>
  );
}
