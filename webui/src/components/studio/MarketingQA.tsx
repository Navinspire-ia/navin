import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DefaultButton,
  Dropdown,
  Icon,
  type IDropdownOption,
  MessageBar,
  MessageBarType,
  PrimaryButton,
  Spinner,
  SpinnerSize,
  TextField,
} from "@fluentui/react";
import "@/lib/fluent-icons";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows } from "@react-three/drei";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { Mesh } from "three";
import { useTranslation } from "react-i18next";

import {
  fetchMarketingQAAssets,
  fetchMarketingQAReadiness,
  fetchMarketingQAReport,
  fetchMarketingQAReports,
  overrideMarketingQAReport,
  runMarketingQA,
  type MarketingQAAsset,
  type MarketingQAFinding,
  type MarketingQAReadiness,
  type MarketingQAReport,
  type MarketingQAReportSummary,
  type MarketingQAVerdict,
} from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

type LoadState = "loading" | "ready" | "error";

const verdictTone: Record<MarketingQAVerdict, string> = {
  PASS: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  WARN: "bg-amber-500/14 text-amber-800 dark:text-amber-300",
  BLOCK: "bg-red-500/12 text-red-700 dark:text-red-300",
};

const verdictIcon: Record<MarketingQAVerdict, string> = {
  PASS: "CompletedSolid",
  WARN: "WarningSolid",
  BLOCK: "Blocked2Solid",
};

function QualityOrb({ reducedMotion }: { reducedMotion: boolean }) {
  const mesh = useRef<Mesh>(null);
  useFrame((_, delta) => {
    if (!reducedMotion && mesh.current) mesh.current.rotation.y += delta * 0.35;
  });
  return (
    <>
      <ambientLight intensity={1.1} />
      <directionalLight position={[3, 4, 4]} intensity={2.2} castShadow />
      <mesh ref={mesh} rotation={[0.35, 0.4, 0]} castShadow>
        <icosahedronGeometry args={[1, 1]} />
        <meshStandardMaterial color="#6750a4" metalness={0.38} roughness={0.24} />
      </mesh>
      <mesh position={[0, 0, 0.75]} scale={0.32}>
        <torusGeometry args={[0.7, 0.18, 20, 48]} />
        <meshStandardMaterial color="#d8c7ff" metalness={0.2} roughness={0.3} />
      </mesh>
      <ContactShadows position={[0, -1.25, 0]} opacity={0.32} scale={5} blur={2.2} />
    </>
  );
}

function VerdictBadge({ verdict }: { verdict: MarketingQAVerdict }) {
  return (
    <span
      className={cn(
        "inline-flex min-h-7 items-center gap-1.5 rounded-full px-2.5 text-[11px] font-bold tracking-wide",
        verdictTone[verdict],
      )}
    >
      <Icon iconName={verdictIcon[verdict]} aria-hidden />
      {verdict}
    </span>
  );
}

function FindingRow({ finding }: { finding: MarketingQAFinding }) {
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ type: "spring", duration: 0.3, bounce: 0 }}
      className="rounded-xl bg-muted/35 p-3 shadow-[0_1px_1px_rgba(0,0,0,0.04),0_6px_16px_rgba(0,0,0,0.05)]"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <VerdictBadge verdict={finding.status} />
          <span className="truncate text-[13px] font-semibold text-foreground">
            {finding.check.replaceAll("_", " ")}
          </span>
          <span className="text-[11px] text-muted-foreground">{finding.source}</span>
        </div>
        <span className="font-mono text-[12px] tabular-nums text-foreground">
          {Math.round(finding.score)}/100
        </span>
      </div>
      <ul className="mt-2 space-y-1 pl-4 text-[12px] leading-relaxed text-muted-foreground">
        {finding.evidence.map((evidence) => (
          <li key={evidence} className="list-disc text-pretty">
            {evidence}
          </li>
        ))}
      </ul>
      {finding.recommendation ? (
        <p className="mt-2 text-[12px] leading-relaxed text-foreground/85">
          {finding.recommendation}
        </p>
      ) : null}
    </motion.li>
  );
}

export function MarketingQA({ projectPath }: { projectPath?: string | null }) {
  const { token } = useClient();
  const { t, i18n } = useTranslation();
  const reducedMotion = Boolean(useReducedMotion());
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [readiness, setReadiness] = useState<MarketingQAReadiness | null>(null);
  const [assets, setAssets] = useState<MarketingQAAsset[]>([]);
  const [reports, setReports] = useState<MarketingQAReportSummary[]>([]);
  const [candidate, setCandidate] = useState("");
  const [reference, setReference] = useState("");
  const [selected, setSelected] = useState<MarketingQAReport | null>(null);
  const [running, setRunning] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideVerdict, setOverrideVerdict] = useState<MarketingQAVerdict>("PASS");
  const [overriding, setOverriding] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoadState("loading");
    setError("");
    try {
      const [readyPayload, assetPayload, reportPayload] = await Promise.all([
        fetchMarketingQAReadiness(token, projectPath),
        fetchMarketingQAAssets(token, projectPath),
        fetchMarketingQAReports(token, projectPath),
      ]);
      setReadiness(readyPayload);
      setAssets(assetPayload.assets);
      setReports(reportPayload.reports);
      setCandidate((current) =>
        assetPayload.assets.some((asset) => asset.path === current)
          ? current
          : (assetPayload.assets[0]?.path ?? ""),
      );
      setLoadState("ready");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setLoadState("error");
    }
  }, [projectPath, token]);

  useEffect(() => {
    void load();
  }, [load]);

  const assetOptions = useMemo<IDropdownOption[]>(
    () => assets.map((asset) => ({ key: asset.path, text: asset.path })),
    [assets],
  );
  const referenceOptions = useMemo<IDropdownOption[]>(
    () => [
      { key: "", text: t("studio.marketingQA.noReference") },
      ...assetOptions.filter((item) => item.key !== candidate),
    ],
    [assetOptions, candidate, t],
  );

  const openReport = useCallback(
    async (reportId: string) => {
      if (!token) return;
      setDetailLoading(true);
      setError("");
      try {
        setSelected(await fetchMarketingQAReport(token, reportId, projectPath));
        setOverrideReason("");
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setDetailLoading(false);
      }
    },
    [projectPath, token],
  );

  const runCheck = useCallback(async () => {
    if (!token || !candidate) return;
    setRunning(true);
    setError("");
    try {
      const report = await runMarketingQA(
        token,
        {
          candidate,
          references: reference ? [reference] : [],
          claims: reference
            ? [
                "product_fidelity",
                "logo_fidelity",
                "text_accuracy",
                "color_fidelity",
                "composition",
              ]
            : ["composition"],
        },
        projectPath,
      );
      setSelected(report);
      const next = await fetchMarketingQAReports(token, projectPath);
      setReports(next.reports);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRunning(false);
    }
  }, [candidate, projectPath, reference, token]);

  const applyOverride = useCallback(async () => {
    if (!token || !selected || overrideReason.trim().length < 3) return;
    setOverriding(true);
    setError("");
    try {
      const report = await overrideMarketingQAReport(
        token,
        selected.id,
        overrideReason.trim(),
        overrideVerdict,
        projectPath,
      );
      setSelected(report);
      setOverrideReason("");
      const next = await fetchMarketingQAReports(token, projectPath);
      setReports(next.reports);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setOverriding(false);
    }
  }, [overrideReason, overrideVerdict, projectPath, selected, token]);

  if (loadState === "loading") {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center" role="status">
        <Spinner
          size={SpinnerSize.medium}
          label={t("studio.marketingQA.loading")}
        />
      </div>
    );
  }

  if (loadState === "error") {
    return (
      <div className="mx-auto flex w-full max-w-xl flex-1 items-center px-5">
        <MessageBar
          messageBarType={MessageBarType.error}
          actions={
            <DefaultButton
              iconProps={{ iconName: "Refresh" }}
              text={t("studio.marketingQA.retry")}
              onClick={() => void load()}
            />
          }
        >
          {error || t("studio.marketingQA.loadError")}
        </MessageBar>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(circle_at_top_left,rgba(103,80,164,0.10),transparent_38%)]">
      <div className="mx-auto grid w-full max-w-[1440px] gap-4 p-4 lg:grid-cols-[minmax(300px,0.8fr)_minmax(420px,1.2fr)] lg:p-5">
        <div className="space-y-4">
          <motion.section
            initial={reducedMotion ? false : { opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", duration: 0.3, bounce: 0 }}
            className="relative overflow-hidden rounded-2xl bg-background p-4 shadow-[0_1px_2px_rgba(0,0,0,0.05),0_14px_40px_rgba(0,0,0,0.08)]"
          >
            <div className="grid grid-cols-[1fr_112px] items-center gap-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-violet-600 dark:text-violet-300">
                  {t("studio.marketingQA.eyebrow")}
                </p>
                <h2 className="mt-1 text-balance text-lg font-semibold text-foreground">
                  {t("studio.marketingQA.title")}
                </h2>
                <p className="mt-1 text-pretty text-[12px] leading-relaxed text-muted-foreground">
                  {t("studio.marketingQA.subtitle")}
                </p>
              </div>
              <div className="h-28 overflow-hidden rounded-xl bg-violet-950/[0.06]">
                <Canvas
                  role="img"
                  aria-label={t("studio.marketingQA.sceneAria")}
                  dpr={[1, 2]}
                  shadows
                  frameloop={reducedMotion ? "demand" : "always"}
                  camera={{ position: [0, 0.2, 4], fov: 52 }}
                >
                  <Suspense fallback={null}>
                    <QualityOrb reducedMotion={reducedMotion} />
                  </Suspense>
                </Canvas>
              </div>
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              <div className="rounded-xl bg-muted/35 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[12px] font-semibold text-foreground">
                    {readiness?.dependency.name ?? "Pillow"}
                  </span>
                  <Icon
                    iconName={readiness?.dependency.ready ? "CompletedSolid" : "Blocked2Solid"}
                    className={
                      readiness?.dependency.ready ? "text-emerald-600" : "text-red-600"
                    }
                    aria-hidden
                  />
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {readiness?.dependency.ready
                    ? t("studio.marketingQA.ready")
                    : t("studio.marketingQA.notReady")}
                </p>
              </div>
              {(readiness?.providers ?? []).map((provider) => (
                <div key={provider.name} className="rounded-xl bg-muted/35 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[12px] font-semibold text-foreground">
                      {provider.name} {provider.model ? `- ${provider.model}` : ""}
                    </span>
                    <Icon
                      iconName={provider.ready ? "CompletedSolid" : "WarningSolid"}
                      className={provider.ready ? "text-emerald-600" : "text-amber-600"}
                      aria-hidden
                    />
                  </div>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {provider.ready
                      ? t("studio.marketingQA.providerReady")
                      : t("studio.marketingQA.providerMissing")}
                  </p>
                </div>
              ))}
            </div>
          </motion.section>

          <section className="rounded-2xl bg-background p-4 shadow-[0_1px_2px_rgba(0,0,0,0.05),0_12px_32px_rgba(0,0,0,0.07)]">
            <div className="flex items-center gap-2">
              <Icon iconName="TestBeaker" className="text-violet-600" aria-hidden />
              <h2 className="text-balance text-[14px] font-semibold text-foreground">
                {t("studio.marketingQA.runTitle")}
              </h2>
            </div>
            {assets.length ? (
              <div className="mt-3 space-y-3">
                <Dropdown
                  label={t("studio.marketingQA.candidate")}
                  selectedKey={candidate}
                  options={assetOptions}
                  onChange={(_, option) => setCandidate(String(option?.key ?? ""))}
                  required
                />
                <Dropdown
                  label={t("studio.marketingQA.reference")}
                  selectedKey={reference}
                  options={referenceOptions}
                  onChange={(_, option) => setReference(String(option?.key ?? ""))}
                />
                <PrimaryButton
                  iconProps={{ iconName: running ? "Sync" : "Play" }}
                  text={
                    running
                      ? t("studio.marketingQA.running")
                      : t("studio.marketingQA.run")
                  }
                  disabled={running || !candidate}
                  onClick={() => void runCheck()}
                  styles={{ root: { minHeight: 40, borderRadius: 8 } }}
                />
                {!readiness?.ready ? (
                  <p className="text-pretty text-[11px] leading-relaxed text-amber-700 dark:text-amber-300">
                    {t("studio.marketingQA.degradedHint")}
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="mt-3 rounded-xl bg-muted/35 p-4 text-center">
                <Icon iconName="PhotoCollection" className="text-xl text-muted-foreground" />
                <p className="mt-2 text-[12px] text-muted-foreground">
                  {t("studio.marketingQA.noAssets")}
                </p>
              </div>
            )}
          </section>

          <section className="rounded-2xl bg-background p-4 shadow-[0_1px_2px_rgba(0,0,0,0.05),0_12px_32px_rgba(0,0,0,0.07)]">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-balance text-[14px] font-semibold text-foreground">
                {t("studio.marketingQA.recent")}
              </h2>
              <DefaultButton
                iconProps={{ iconName: "Refresh" }}
                ariaLabel={t("studio.marketingQA.refresh")}
                title={t("studio.marketingQA.refresh")}
                onClick={() => void load()}
                styles={{ root: { minWidth: 40, minHeight: 40, borderRadius: 8 } }}
              />
            </div>
            {reports.length ? (
              <div className="mt-3 space-y-2">
                {reports.map((report) => (
                  <button
                    key={report.id}
                    type="button"
                    onClick={() => void openReport(report.id)}
                    className={cn(
                      "flex min-h-12 w-full items-center gap-3 rounded-xl bg-muted/30 px-3 py-2 text-left shadow-[0_1px_1px_rgba(0,0,0,0.04)]",
                      "transition-[background-color,transform] duration-200 hover:bg-muted/60 active:scale-[0.96]",
                      "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500",
                      selected?.id === report.id && "ring-2 ring-violet-500/45",
                    )}
                  >
                    <VerdictBadge verdict={report.effective_verdict} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[12px] font-semibold text-foreground">
                        {report.candidate.path}
                      </p>
                      <p className="mt-0.5 text-[10.5px] text-muted-foreground">
                        {new Intl.DateTimeFormat(i18n.language, {
                          dateStyle: "medium",
                          timeStyle: "short",
                        }).format(new Date(report.created_at))}
                      </p>
                    </div>
                    <span className="font-mono text-[12px] tabular-nums text-foreground">
                      {Math.round(report.score)}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="mt-3 rounded-xl bg-muted/35 p-4 text-center text-[12px] text-muted-foreground">
                {t("studio.marketingQA.noReports")}
              </p>
            )}
          </section>
        </div>

        <section className="min-h-[480px] rounded-2xl bg-background p-4 shadow-[0_1px_2px_rgba(0,0,0,0.05),0_16px_44px_rgba(0,0,0,0.08)] lg:p-5">
          {error ? (
            <MessageBar
              messageBarType={MessageBarType.error}
              onDismiss={() => setError("")}
              dismissButtonAriaLabel={t("studio.marketingQA.dismissError")}
            >
              {error}
            </MessageBar>
          ) : null}
          {detailLoading ? (
            <div className="flex h-full min-h-72 items-center justify-center" role="status">
              <Spinner label={t("studio.marketingQA.loadingReport")} />
            </div>
          ) : selected ? (
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={`${selected.id}-${selected.effective_verdict}`}
                initial={reducedMotion ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ type: "spring", duration: 0.3, bounce: 0 }}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                      {t("studio.marketingQA.report")}
                    </p>
                    <h2 className="mt-1 truncate text-balance text-lg font-semibold text-foreground">
                      {selected.candidate.path}
                    </h2>
                    <p className="mt-1 text-[11px] text-muted-foreground">{selected.id}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <VerdictBadge verdict={selected.effective_verdict} />
                    <span className="font-mono text-xl font-semibold tabular-nums text-foreground">
                      {Math.round(selected.scores.overall)}
                    </span>
                  </div>
                </div>

                {selected.human_override ? (
                  <MessageBar messageBarType={MessageBarType.info} className="mt-4">
                    {t("studio.marketingQA.overrideApplied", {
                      verdict: selected.human_override.verdict,
                      reason: selected.human_override.reason,
                    })}
                  </MessageBar>
                ) : null}

                <div className="mt-4 grid gap-2 sm:grid-cols-3">
                  {(["deterministic", "vision", "overall"] as const).map((key) => (
                    <div key={key} className="rounded-xl bg-muted/35 p-3">
                      <p className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
                        {t(`studio.marketingQA.scores.${key}`)}
                      </p>
                      <p className="mt-1 font-mono text-lg font-semibold tabular-nums text-foreground">
                        {Math.round(selected.scores[key])}
                      </p>
                    </div>
                  ))}
                </div>

                <div className="mt-5">
                  <h3 className="text-balance text-[14px] font-semibold text-foreground">
                    {t("studio.marketingQA.findings", {
                      count: selected.findings.length,
                    })}
                  </h3>
                  <ul className="mt-3 space-y-2">
                    <AnimatePresence initial={false}>
                      {selected.findings.map((finding, index) => (
                        <FindingRow
                          key={`${finding.source}-${finding.check}-${index}`}
                          finding={finding}
                        />
                      ))}
                    </AnimatePresence>
                  </ul>
                </div>

                <div className="mt-5 rounded-2xl bg-violet-500/[0.07] p-4">
                  <div className="flex items-center gap-2">
                    <Icon iconName="UserWarning" className="text-violet-700" aria-hidden />
                    <h3 className="text-balance text-[14px] font-semibold text-foreground">
                      {t("studio.marketingQA.overrideTitle")}
                    </h3>
                  </div>
                  <p className="mt-1 text-pretty text-[11px] leading-relaxed text-muted-foreground">
                    {t("studio.marketingQA.overrideHint")}
                  </p>
                  <div className="mt-3 grid gap-3 sm:grid-cols-[160px_1fr]">
                    <Dropdown
                      label={t("studio.marketingQA.overrideVerdict")}
                      selectedKey={overrideVerdict}
                      options={(["PASS", "WARN", "BLOCK"] as const).map((verdict) => ({
                        key: verdict,
                        text: verdict,
                      }))}
                      onChange={(_, option) =>
                        setOverrideVerdict(
                          (option?.key as MarketingQAVerdict | undefined) ?? "PASS",
                        )
                      }
                    />
                    <TextField
                      label={t("studio.marketingQA.overrideReason")}
                      value={overrideReason}
                      onChange={(_, value) => setOverrideReason(value ?? "")}
                      placeholder={t("studio.marketingQA.overridePlaceholder")}
                      required
                      errorMessage={
                        overrideReason.length > 0 && overrideReason.trim().length < 3
                          ? t("studio.marketingQA.overrideRequired")
                          : undefined
                      }
                    />
                  </div>
                  <PrimaryButton
                    className="mt-3"
                    iconProps={{ iconName: "ComplianceAudit" }}
                    text={
                      overriding
                        ? t("studio.marketingQA.overriding")
                        : t("studio.marketingQA.overrideAction")
                    }
                    disabled={overriding || overrideReason.trim().length < 3}
                    onClick={() => void applyOverride()}
                    styles={{ root: { minHeight: 40, borderRadius: 8 } }}
                  />
                </div>
              </motion.div>
            </AnimatePresence>
          ) : (
            <div className="flex min-h-[440px] flex-col items-center justify-center text-center">
              <Icon iconName="ReportDocument" className="text-4xl text-violet-500/70" />
              <h2 className="mt-4 text-balance text-[15px] font-semibold text-foreground">
                {t("studio.marketingQA.emptyDetailTitle")}
              </h2>
              <p className="mt-1 max-w-sm text-pretty text-[12px] leading-relaxed text-muted-foreground">
                {t("studio.marketingQA.emptyDetail")}
              </p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
