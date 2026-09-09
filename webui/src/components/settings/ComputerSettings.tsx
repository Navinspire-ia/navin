import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import {
  Customizer, DefaultButton, Dropdown, Icon, MessageBar, MessageBarType, PrimaryButton,
  Pivot, PivotItem, Separator, Spinner, Stack, Text, TextField, Toggle, createTheme,
} from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { useThemeValue } from "@/hooks/useTheme";
import {
  fetchComputerDiagnostics, fetchComputerModels, requestComputerPermission, setComputerStopped,
  updateComputerModel, updateSettings,
} from "@/lib/api";
import type { ComputerDiagnostics, ComputerModelsPayload, SettingsPayload, SettingsUpdate } from "@/lib/types";
import { COMPUTER_PERMISSIONS, computerChatUrl, computerPermissionGranted, computerSetupStep, type ComputerPermission } from "@/lib/computer-setup";
import { useClient } from "@/providers/ClientProvider";
import "@/lib/fluent-icons";
import "./computer-settings.css";

type ComputerConfig = NonNullable<SettingsPayload["computer"]>;
type AppRule = "protected_apps" | "allowed_apps" | "blocked_apps" | "ask_apps";
// Model catalogs run to dozens of rows: the list scrolls inside the callout
// instead of running past the top or bottom of the window.
const MODEL_LIST_CALLOUT = { calloutMaxHeight: 320 };
const APP_RULES: AppRule[] = ["protected_apps", "allowed_apps", "blocked_apps", "ask_apps"];

function ToggleRow({ title, description, checked, disabled, onChange }: {
  title: string; description: string; checked: boolean; disabled: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="computer-settings-toggle-row">
      <div>
        <Text block styles={{ root: { fontWeight: 600 } }}>{title}</Text>
        <Text block variant="small" className="computer-settings-description">{description}</Text>
      </div>
      <Toggle ariaLabel={title} checked={checked} disabled={disabled}
        onChange={(_, value) => onChange(!!value)} styles={{ root: { marginBottom: 0 } }} />
    </div>
  );
}

function NumberSetting({ label, value, min, max, disabled, onSave }: {
  label: string; value: number; min: number; max: number; disabled: boolean;
  onSave: (next: number) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(String(value));
  const [error, setError] = useState<string>();
  useEffect(() => { setDraft(String(value)); setError(undefined); }, [value]);
  return (
    <TextField label={label} type="number" min={min} max={max} value={draft}
      disabled={disabled} errorMessage={error}
      onChange={(_, next) => setDraft(next ?? "")}
      onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
      onBlur={() => {
        const next = Number(draft);
        if (!draft.trim() || !Number.isInteger(next) || next < min || next > max) {
          setError(t("settings.computer.numberRange", { min, max }));
          return;
        }
        setError(undefined);
        if (next !== value) void onSave(next);
      }} />
  );
}

export function ComputerSettings({ settings, onUpdated, onRestart, isRestarting }: {
  settings: SettingsPayload;
  onUpdated: Dispatch<SetStateAction<SettingsPayload | null>>;
  onRestart: () => Promise<void>;
  isRestarting: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string) => t(`settings.computer.${key}`);
  const { token } = useClient();
  const dark = useThemeValue() === "dark";
  const reduced = useReducedMotion();
  const theme = useMemo(() => createTheme({
    isInverted: dark,
    palette: dark ? {
      themePrimary: "#62abf5", white: "#17191d", black: "#f5f5f5",
      neutralLighterAlt: "#1b1e23", neutralLighter: "#22262c", neutralLight: "#303640",
      neutralQuaternaryAlt: "#404650", neutralQuaternary: "#505864", neutralTertiaryAlt: "#67717f",
      neutralTertiary: "#a1aab5", neutralSecondary: "#c2c8d0", neutralPrimaryAlt: "#e2e6ec",
      neutralPrimary: "#f5f5f5", neutralDark: "#ffffff",
    } : { themePrimary: "#0078d4" },
    defaultFontStyle: { fontFamily: "inherit" },
  }), [dark]);
  const cfg = settings.computer;
  const hasConfig = !!cfg;
  const configuredProviders = settings.providers.filter((provider) => provider.configured && provider.model_selectable !== false);
  const providerNames = configuredProviders.map((provider) => provider.name).join("|");
  const savedProvider = cfg?.model_provider || settings.agent.resolved_provider || settings.agent.provider;
  const [provider, setProvider] = useState(savedProvider);
  const [models, setModels] = useState<ComputerModelsPayload | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelsRefresh, setModelsRefresh] = useState(0);
  const [view, setView] = useState("desktop");
  const [busy, setBusy] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [needsRestart, setNeedsRestart] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [diagnostics, setDiagnostics] = useState<ComputerDiagnostics | null>(null);
  const [awaitingPermissions, setAwaitingPermissions] = useState(false);
  const diagnosticsRequest = useRef(0);
  const setupRoot = useRef<HTMLElement>(null);
  const [display, setDisplay] = useState(cfg?.display ?? "");
  const [apps, setApps] = useState<Record<AppRule, string>>({
    protected_apps: "", allowed_apps: "", blocked_apps: "", ask_apps: "",
  });

  useEffect(() => { setProvider(savedProvider); }, [savedProvider]);
  useEffect(() => {
    const names = providerNames.split("|").filter(Boolean);
    if (!names.includes(provider)) setProvider(names[0] ?? "");
  }, [provider, providerNames]);
  useEffect(() => {
    let cancelled = false;
    setModels(null); setModelsError(null);
    if (!provider || !token) { setModelsLoading(false); return; }
    setModelsLoading(true);
    void fetchComputerModels(token, provider).then((payload) => {
      if (!cancelled) setModels(payload);
    }).catch((reason) => {
      if (!cancelled) setModelsError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => { if (!cancelled) setModelsLoading(false); });
    return () => { cancelled = true; };
  }, [provider, token, modelsRefresh]);

  useEffect(() => {
    const request = ++diagnosticsRequest.current;
    setDiagnostics(null);
    if (token && cfg?.enabled) {
      void fetchComputerDiagnostics(token, true).then((payload) => {
        if (request === diagnosticsRequest.current) setDiagnostics(payload);
      }).catch(() => { /* The explicit check offers a retry with context. */ });
    }
    return () => { diagnosticsRequest.current += 1; };
  }, [token, cfg?.backend, cfg?.display, cfg?.enabled]);

  useEffect(() => {
    if (!awaitingPermissions || busy || cfg?.backend !== "macos" || !token) return;
    let active = true;
    let checking = false;
    const refresh = async () => {
      if (document.visibilityState === "hidden" || checking) return;
      checking = true;
      const request = ++diagnosticsRequest.current;
      try {
        const payload = await fetchComputerDiagnostics(token, true);
        if (active && request === diagnosticsRequest.current) setDiagnostics(payload);
      } catch { /* Keep the visible retry button available. */ }
      finally { checking = false; }
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      active = false;
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [awaitingPermissions, busy, cfg?.backend, token]);

  useEffect(() => {
    if (!hasConfig) return;
    const step = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("step");
    if (step && ["model", "permissions", "check"].includes(step)) {
      setupRoot.current?.querySelector<HTMLElement>(`[data-setup-step="${step}"]`)?.focus();
    }
  }, [hasConfig]);

  useEffect(() => { setDisplay(cfg?.display ?? ""); }, [cfg?.display]);
  useEffect(() => {
    setApps(Object.fromEntries(APP_RULES.map((rule) => [rule, (cfg?.[rule] ?? []).join("\n")])) as Record<AppRule, string>);
  }, [cfg?.protected_apps, cfg?.allowed_apps, cfg?.blocked_apps, cfg?.ask_apps]);

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name); setError(null); setSaved(false);
    try { await action(); }
    catch { setError(tx(name === "permissions" ? "permissionRequestFailed" : name === "doctor" ? "checkFailed" : "saveFailed")); }
    finally { setBusy(null); }
  }

  async function persist(update: SettingsUpdate) {
    await run("save", async () => {
      const payload = await updateSettings(token, update);
      onUpdated(payload);
      if (update.computerEnabled === true && payload.requires_restart) setNeedsRestart(true);
      if (update.computerEnabled === false) setNeedsRestart(false);
      setSaved(true);
    });
  }

  async function toggleStop() {
    setStopping(true); setError(null);
    try {
      const result = await setComputerStopped(token, !cfg?.stopped);
      onUpdated((previous) => previous?.computer
        ? { ...previous, computer: { ...previous.computer, stopped: result.stopped } } : previous);
      diagnosticsRequest.current += 1;
      setDiagnostics((previous) => previous ? { ...previous, ready: false, stopped: result.stopped } : null);
    } catch { setError(tx("controlFailed")); }
    finally { setStopping(false); }
  }

  async function selectModel(model: string) {
    await run("save", async () => {
      onUpdated(await updateComputerModel(token, provider, model));
      setSaved(true);
    });
  }

  async function checkDesktop(passive = false) {
    await run("doctor", async () => {
      const request = ++diagnosticsRequest.current;
      const payload = await fetchComputerDiagnostics(token, passive);
      if (request === diagnosticsRequest.current) setDiagnostics(payload);
      if (payload.ready) setAwaitingPermissions(false);
    });
  }

  async function requestPermissions(kind: ComputerPermission | "all") {
    setAwaitingPermissions(true);
    await run("permissions", async () => {
      const request = ++diagnosticsRequest.current;
      const payload = await requestComputerPermission(token, kind);
      if (request === diagnosticsRequest.current) setDiagnostics(payload);
      if (payload.ready) setAwaitingPermissions(false);
    });
  }

  if (!cfg) return <MessageBar>{tx("unavailable")}</MessageBar>;

  const disabled = !!busy || stopping || isRestarting || !token;
  const optionDisabled = disabled || !cfg.enabled;
  const modelReady = cfg.model_vision === true && configuredProviders.some((entry) => entry.name === savedProvider);
  const setupStep = cfg.enabled && !cfg.stopped && !modelReady ? "model" : computerSetupStep(cfg, diagnostics);
  const mac = cfg.backend === "macos";
  const accessReady = mac ? COMPUTER_PERMISSIONS.every((kind) => computerPermissionGranted(diagnostics, kind) === true) : !!cfg.backend && cfg.backend !== "none";
  const canCheck = cfg.enabled && modelReady && !cfg.stopped && cfg.backend !== "none";
  const recommended = models?.models.find((row) => row.id === models.recommended);
  const recommendedSelected = provider === savedProvider && recommended?.id === cfg.model && modelReady;
  const rulesDirty = APP_RULES.some((rule) => apps[rule] !== (cfg[rule] ?? []).join("\n"));
  const numbers: Array<[keyof ComputerConfig, keyof SettingsUpdate, string, number, number, number]> = [
    ["settle_ms", "computerSettleMs", "settle", 400, 0, 5000],
    ["type_delay_ms", "computerTypeDelayMs", "typeDelay", 6, 0, 200],
    ["max_actions_per_turn", "computerMaxActionsPerTurn", "maxActions", 150, 1, 2000],
    ["screenshot_max_width", "computerScreenshotMaxWidth", "captureWidth", 1366, 480, 3840],
    ["screenshot_max_height", "computerScreenshotMaxHeight", "captureHeight", 768, 320, 2160],
    ["user_takeover_px", "computerUserTakeoverPx", "takeoverThreshold", 48, 0, 2000],
  ];

  return (
    <Customizer settings={{ theme }}>
      <motion.section ref={setupRoot} className="computer-settings" data-testid="computer-settings"
        initial={reduced ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.18 }}>
        <Stack tokens={{ childrenGap: 24 }}>
          <div className="computer-settings-heading">
            <div>
              <Text as="h2" variant="xLarge" styles={{ root: { margin: 0, fontWeight: 600 } }}>Computer</Text>
              <Text block className="computer-settings-description">{tx("intro")}</Text>
            </div>
            {view === "desktop" ? <Text className={`computer-settings-state ${setupStep === "ready" ? "is-enabled" : ""}`}>
              {tx(`setupState.${setupStep}`)}
            </Text> : null}
          </div>

          <Pivot selectedKey={view} onLinkClick={(item) => setView(item?.props.itemKey ?? "desktop")}>
            <PivotItem itemKey="desktop" headerText={t("settings.sections.computer")} itemIcon="TVMonitor" />
            <PivotItem itemKey="browser" headerText={t("settings.sections.browser")} itemIcon="Globe" />
          </Pivot>

          <div aria-live="polite">
            {error ? <MessageBar messageBarType={MessageBarType.error} isMultiline>{error}</MessageBar>
              : busy ? <Spinner label={busy === "save" ? tx("saving") : busy === "permissions" ? tx("openingPermissions") : tx("checking")} labelPosition="right" />
                : saved ? <MessageBar messageBarType={MessageBarType.success}>{tx("saved")}</MessageBar> : null}
          </div>

          {view === "desktop" ? <>
          <div className="computer-setup-progress" role="list" aria-label={tx("setupProgress")}>
            {(["model", "permissions", "check"] as const).map((step, index) => {
              const complete = step === "model" ? modelReady : step === "permissions" ? accessReady : setupStep === "ready";
              return <div role="listitem" key={step}>
                <DefaultButton text={`${index + 1}. ${tx(`steps.${step}`)}`} checked={setupStep === step}
                  iconProps={{ iconName: complete ? "Completed" : "CircleRing" }}
                  onClick={() => setupRoot.current?.querySelector<HTMLElement>(`[data-setup-step="${step}"]`)?.focus()} />
              </div>;
            })}
          </div>
          <div>
            <ToggleRow title={t("settings.rows.computerEnabled")} description={tx("enabledHelp")}
              checked={cfg.enabled} disabled={disabled} onChange={(next) => void persist({ computerEnabled: next })} />
            {needsRestart ? <MessageBar actions={
              <DefaultButton text={tx("restart")} disabled={isRestarting}
                onClick={() => void run("restart", async () => { await onRestart(); setNeedsRestart(false); })} />
            }>{tx("restartHelp")}</MessageBar> : null}
            {cfg.stopped ? <MessageBar messageBarType={MessageBarType.warning} actions={
              <DefaultButton text={tx("go")} disabled={disabled} onClick={() => void toggleStop()} />
            }>{tx("stoppedHelp")}</MessageBar> : null}
          </div>

          <section className="computer-settings-card" data-setup-step="model" tabIndex={-1} aria-label={tx("modelTitle")}>
            <Text as="h3" variant="large" styles={{ root: { margin: 0, fontWeight: 600 } }}>{tx("modelTitle")}</Text>
            <Text block className="computer-settings-description">{tx("modelHelp")}</Text>
            <MessageBar messageBarType={modelReady ? MessageBarType.success : MessageBarType.warning}>
              {modelReady ? t("settings.computer.modelCompatible", { model: cfg.model })
                : t("settings.computer.modelIncompatible", { model: cfg.model || tx("noModel") })}
            </MessageBar>
            <div className="computer-settings-grid">
              <Dropdown label={tx("provider")} selectedKey={provider || null} disabled={disabled}
                calloutProps={MODEL_LIST_CALLOUT}
                placeholder={tx("chooseProvider")} options={configuredProviders.map((entry) => ({ key: entry.name, text: entry.label }))}
                onChange={(_, option) => { if (option) setProvider(String(option.key)); }} />
              <Dropdown label={t("settings.rows.computerRoute")}
                selectedKey={provider === savedProvider && models?.models.some((row) => row.id === cfg.model) ? cfg.model : null}
                calloutProps={MODEL_LIST_CALLOUT}
                disabled={disabled || modelsLoading || !models?.models.length}
                placeholder={modelsLoading ? tx("loadingModels") : tx("chooseModel")}
                options={(models?.models ?? []).map((row) => ({ key: row.id,
                  text: row.label + (row.id === models?.recommended ? ` (${tx("recommended")})` : "") }))}
                onChange={(_, option) => { if (option) void selectModel(String(option.key)); }} />
            </div>
            <Text block variant="small" className="computer-settings-description">{provider === "navin" ? tx("navinModelHelp") : tx("byokModelHelp")}</Text>
            {modelsError || models?.status === "unavailable" ? <MessageBar messageBarType={MessageBarType.warning} actions={
              <DefaultButton text={tx("retry")} disabled={disabled || modelsLoading} onClick={() => setModelsRefresh((value) => value + 1)} />
            }>{tx("catalogUnavailable")}</MessageBar> : null}
            {!modelsLoading && !models?.models.length && !modelsError ? <MessageBar>{configuredProviders.length ? tx("noVisionModels") : tx("noProviders")}</MessageBar> : null}
            <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
              {recommended && !recommendedSelected ? <PrimaryButton text={tx("useRecommended")}
                disabled={disabled || modelsLoading} iconProps={{ iconName: "Wand" }}
                onClick={() => void selectModel(recommended.id)} /> : null}
              <DefaultButton text={tx("configureProvider")} onClick={() => {
                const query = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
                query.set("section", "providers"); query.delete("step");
                window.location.hash = `#/settings?${query}`;
              }} />
            </Stack>
            {modelReady && cfg.model_grounding === false ? <Text block variant="small" className="computer-settings-description">{tx("visionOnlyHelp")}</Text> : null}
          </section>

          <section className="computer-settings-card" data-setup-step="permissions" tabIndex={-1} aria-label={tx("permissionsTitle")}>
            <Text as="h3" variant="large" styles={{ root: { margin: 0, fontWeight: 600 } }}>{tx("permissionsTitle")}</Text>
            <Text block>{t(`settings.computer.platforms.${cfg.backend}`, { defaultValue: cfg.backend || tx("unavailable") })}</Text>
            {mac ? <>
              <Text block className="computer-settings-description">{tx("macPermissions")}</Text>
              <ul className="computer-permission-list">
                {COMPUTER_PERMISSIONS.map((kind) => {
                  const granted = computerPermissionGranted(diagnostics, kind);
                  return <li key={kind} data-testid={`computer-permission-${kind}`}>
                    <Icon iconName={granted ? "Completed" : "Lock"} aria-hidden />
                    <div className="computer-permission-copy">
                      <Text block styles={{ root: { fontWeight: 600 } }}>{tx(kind)}</Text>
                      <Text block variant="small" className="computer-settings-description">{tx(`permissionHelp.${kind}`)}</Text>
                      <Text block variant="small" className={granted ? "computer-permission-granted" : undefined}>
                        {tx(granted ? "granted" : granted === false ? "permissionNeeded" : "permissionUnknown")}
                      </Text>
                    </div>
                    {!granted ? <DefaultButton text={tx("allow")} ariaLabel={t("settings.computer.allowNamed", { permission: tx(kind) })}
                      disabled={optionDisabled} onClick={() => void requestPermissions(kind)} /> : null}
                  </li>;
                })}
              </ul>
              <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
                {!accessReady ? <PrimaryButton text={awaitingPermissions ? tx("continuePermissions") : tx("preparePermissions")}
                  iconProps={{ iconName: "Permissions" }} disabled={optionDisabled} onClick={() => void requestPermissions("all")} /> : null}
                <DefaultButton text={tx("recheckPermissions")} disabled={disabled} onClick={() => void checkDesktop(true)} />
              </Stack>
              {awaitingPermissions && !accessReady ? <MessageBar>{tx("permissionReturn")}</MessageBar> : null}
            </> : <MessageBar messageBarType={cfg.backend === "none" ? MessageBarType.warning : MessageBarType.info}>
              {tx(cfg.backend === "windows" ? "windowsPermissions" : cfg.backend === "wayland" ? "waylandPermissions" : cfg.backend === "x11" ? "x11Permissions" : "noDesktop")}
            </MessageBar>}
            {cfg.backend_notes?.map((note) => <Text block variant="small" key={note}>{note}</Text>)}
          </section>

          <section className="computer-settings-card" data-setup-step="check" tabIndex={-1} aria-label={tx("checkTitle")}>
            <Text as="h3" variant="large" styles={{ root: { margin: 0, fontWeight: 600 } }}>{tx("checkTitle")}</Text>
            <Text block className="computer-settings-description">{tx("checkHelp")}</Text>
            {!modelReady ? <Text block>{tx("chooseModelFirst")}</Text> : null}
            <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
              <PrimaryButton text={tx("check")} iconProps={{ iconName: "CheckMark" }} disabled={disabled || !canCheck}
                onClick={() => void checkDesktop()} />
              <DefaultButton text={cfg.stopped ? tx("go") : tx("stop")}
                iconProps={{ iconName: cfg.stopped ? "Play" : "StopSolid" }} disabled={stopping || !token}
                onClick={() => void toggleStop()} />
            </Stack>
            {diagnostics && !diagnostics.passive ? <>
              <MessageBar messageBarType={setupStep === "ready" ? MessageBarType.success : MessageBarType.warning}>
                {setupStep === "ready" ? tx("ready") : setupStep === "permissions" ? tx("finishPermissions") : tx("checkResults")}
              </MessageBar>
              {setupStep === "ready" ? <PrimaryButton text={tx("openChat")} iconProps={{ iconName: "Chat" }}
                onClick={() => { window.location.hash = computerChatUrl(window.location.hash); }} /> : null}
              <details className="computer-check-details">
                <summary>{tx("checkDetails")}</summary>
                <ul className="computer-settings-checks">
                  {diagnostics.checks.map((check, index) => <li key={`${check.name}-${index}`}>
                    <Icon className={`computer-settings-check-mark ${check.ok ? "is-ok" : ""}`} iconName={check.ok ? "Completed" : "Info"} aria-hidden />
                    <div><Text block styles={{ root: { fontWeight: 600 } }}>{check.optional && check.name === "accessibility" ? tx("elementAccess") : t(`settings.computer.checkLabels.${check.name}`, { defaultValue: check.name })}{check.optional ? ` (${tx("optional")})` : ""}</Text>
                      <Text block variant="small">{mac && ["screen_recording", "accessibility", "automation"].includes(check.name)
                        ? tx(check.ok ? "granted" : "permissionNeeded") : check.detail.replace(/screenshot/gi, "Screen")}</Text>
                      {check.fix ? <Text block variant="small" className="computer-settings-description">{mac ? tx("finishPermissions") : check.fix.replace(/screenshot/gi, "Screen")}</Text> : null}
                    </div>
                  </li>)}
                </ul>
              </details>
            </> : null}
          </section>

          <div className="computer-settings-card">
            <Text as="h3" variant="large" styles={{ root: { margin: 0, fontWeight: 600 } }}>{tx("controlPreferences")}</Text>
            <div className="computer-settings-grid">
              <Dropdown label={t("settings.rows.computerAsk")} selectedKey={cfg.ask}
                disabled={optionDisabled} options={[
                  { key: "never", text: tx("autonomous") },
                  { key: "destructive", text: tx("askDestructive") },
                  { key: "always", text: tx("askAlways") },
                ]} onChange={(_, option) => { if (option) void persist({ computerAsk: option.key as SettingsUpdate["computerAsk"] }); }} />
              <Dropdown label={t("settings.rows.computerSessionMode")} selectedKey={cfg.session_mode}
                disabled={optionDisabled} options={[
                  { key: "shared", text: tx("sessionShared") }, { key: "dedicated", text: tx("sessionDedicated") },
                ]} onChange={(_, option) => { if (option) void persist({ computerSessionMode: option.key as SettingsUpdate["computerSessionMode"] }); }} />
            </div>
            <Text block variant="small" className="computer-settings-description">{tx("takeoverHelp")}</Text>
            <ToggleRow title={t("settings.rows.computerLiveView")} description={tx("liveHelp")}
              checked={cfg.live_view} disabled={optionDisabled} onChange={(next) => void persist({ computerLiveView: next })} />
            <ToggleRow title={t("settings.rows.computerAuditLog")} description={tx("auditHelp")}
              checked={cfg.audit_log} disabled={optionDisabled} onChange={(next) => void persist({ computerAuditLog: next })} />
          </div>

          <DefaultButton text={advanced ? tx("hideOptions") : tx("moreOptions")}
            aria-expanded={advanced} iconProps={{ iconName: advanced ? "ChevronUp" : "ChevronDown" }}
            onClick={() => setAdvanced((value) => !value)} />
          {advanced ? <div className="computer-settings-card">
            <Text variant="large" styles={{ root: { fontWeight: 600 } }}>{tx("moreOptions")}</Text>
            <div className="computer-settings-grid">
              <Dropdown label={tx("backend")} selectedKey={cfg.backend_preference ?? "auto"} disabled={optionDisabled}
                options={[
                  { key: "auto", text: tx("automatic") }, { key: "windows", text: "Windows" },
                  { key: "macos", text: "macOS" }, { key: "x11", text: "Linux X11" },
                  { key: "wayland", text: "Linux Wayland" }, { key: "none", text: tx("disabled") },
                ]} onChange={(_, option) => { if (option) void persist({ computerBackend: option.key as SettingsUpdate["computerBackend"] }); }} />
              <TextField label={tx("display")} description={tx("displayHelp")} value={display} disabled={optionDisabled}
                onChange={(_, next) => setDisplay(next ?? "")}
                onBlur={() => { if (display !== (cfg.display ?? "")) void persist({ computerDisplay: display }); }} />
              {numbers.map(([field, update, label, fallback, min, max]) => (
                <NumberSetting key={field} label={tx(label)} value={Number(cfg[field] ?? fallback)} min={min} max={max}
                  disabled={optionDisabled} onSave={(value) => persist({ [update]: value })} />
              ))}
            </div>
            <ToggleRow title={tx("auditScreenshots")} description={tx("auditScreenshotsHelp")}
              checked={cfg.audit_screenshots ?? true} disabled={optionDisabled || !cfg.audit_log}
              onChange={(next) => void persist({ computerAuditScreenshots: next })} />
            <ToggleRow title={tx("corner")} description={tx("cornerHelp")}
              checked={cfg.failsafe_corner ?? true} disabled={optionDisabled}
              onChange={(next) => void persist({ computerFailsafeCorner: next })} />
            <ToggleRow title={t("settings.rows.computerAnthropicNative")} description={tx("nativeHelp")}
              checked={cfg.anthropic_native} disabled={optionDisabled}
              onChange={(next) => void persist({ computerAnthropicNative: next })} />
            <Separator />
            <Text variant="large" styles={{ root: { fontWeight: 600 } }}>{tx("apps")}</Text>
            <Text block className="computer-settings-description">{tx("appsHelp")}</Text>
            <div className="computer-settings-grid">
              {APP_RULES.map((rule) => <TextField key={rule} label={tx(rule)} multiline rows={4}
                value={apps[rule]} disabled={optionDisabled}
                onChange={(_, value) => setApps((previous) => ({ ...previous, [rule]: value ?? "" }))} />)}
            </div>
            <PrimaryButton text={tx("saveApps")} disabled={optionDisabled || !rulesDirty}
              onClick={() => { const lines = (rule: AppRule) => apps[rule].split("\n").map((name) => name.trim()).filter(Boolean);
                void persist({ computerProtectedApps: lines("protected_apps"), computerAllowedApps: lines("allowed_apps"),
                  computerBlockedApps: lines("blocked_apps"), computerAskApps: lines("ask_apps") }); }} />
          </div> : null}
          </> : <div className="computer-settings-card">
            <Text variant="large" styles={{ root: { fontWeight: 600 } }}>{t("settings.sections.browser")}</Text>
            <ToggleRow title={t("settings.rows.browserWindow")} description={tx("browserWindowHelp")}
              checked={!(settings.browser?.headless ?? true)} disabled={disabled}
              onChange={(next) => void persist({ browserHeadless: !next })} />
            <ToggleRow title={t("settings.rows.browserLiveView")} description={t("settings.help.browserLiveView")}
              checked={settings.browser?.live_view ?? true} disabled={disabled}
              onChange={(next) => void persist({ browserLiveView: next })} />
          </div>}
        </Stack>
      </motion.section>
    </Customizer>
  );
}
