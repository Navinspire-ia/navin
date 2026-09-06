import { useEffect, useRef, useState, type ReactNode } from "react";
import { ExternalLink, Globe, Loader2, Network, RotateCcw, TerminalSquare } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ExecPolicySettings } from "@/components/settings/ExecPolicySettings";
import { ToggleButton } from "@/components/settings/ToggleButton";
import { Button } from "@/components/ui/button";
import { updateNetworkSafetySettings, updateSettings } from "@/lib/api";
import type {
  NetworkSafetySettingsUpdate,
  SettingsPayload,
  WebuiDefaultAccessMode,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

/**
 * The Settings > Security switches, hosted in the Guardrails panel so the
 * answer to "what may the agent reach" sits next to "what may it do to my
 * repo". Same endpoints as Settings: a change made here shows there, and
 * the other way round.
 */

export function networkSafetyForm(payload: SettingsPayload | null): NetworkSafetySettingsUpdate {
  return {
    webuiAllowLocalServiceAccess:
      payload?.advanced.webui_allow_local_service_access ??
      payload?.advanced.allow_local_preview_access ??
      true,
    webuiDefaultAccessMode:
      payload?.advanced.webui_default_access_mode === "full" ? "full" : "default",
  };
}

export function networkSafetyDirty(
  form: NetworkSafetySettingsUpdate,
  payload: SettingsPayload | null,
): boolean {
  const base = networkSafetyForm(payload);
  return (
    form.webuiAllowLocalServiceAccess !== base.webuiAllowLocalServiceAccess ||
    form.webuiDefaultAccessMode !== base.webuiDefaultAccessMode
  );
}

/**
 * The form to show once a fresh payload arrives: the user's edits when the
 * form differs from the payload it was derived from, the fresh values
 * otherwise. Judging "edited" against the fresh payload instead would flag
 * the defaults shown before the first load as unsaved changes.
 */
export function nextNetworkSafetyForm(
  form: NetworkSafetySettingsUpdate,
  previous: SettingsPayload | null,
  payload: SettingsPayload | null,
): NetworkSafetySettingsUpdate {
  if (networkSafetyDirty(form, previous)) return form;
  return networkSafetyDirty(form, payload) ? networkSafetyForm(payload) : form;
}

/** A saved change the gateway will only apply after a restart. */
export function runtimeRestartPending(payload: SettingsPayload | null): boolean {
  if (!payload) return false;
  if (payload.restart_required_sections?.includes("runtime")) return true;
  return Boolean(payload.requires_restart);
}

export function SecurityCard({
  icon: Icon,
  title,
  children,
  testId,
}: {
  icon: typeof Globe;
  title: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div
      className="overflow-hidden rounded-2xl border border-border/55 bg-card/40"
      data-testid={testId}
    >
      <div className="flex items-center gap-2 border-b border-border/40 px-4 py-2.5">
        <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-[12.5px] font-semibold text-foreground">{title}</span>
      </div>
      {children}
    </div>
  );
}

function SecurityRow({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2.5 border-b border-border/40 px-4 py-3 last:border-b-0 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium leading-5 text-foreground">{title}</p>
        <p className="mt-0.5 whitespace-pre-line text-[12px] leading-5 text-muted-foreground">
          {description}
        </p>
      </div>
      <div className="flex shrink-0 items-center sm:pt-0.5">{children}</div>
    </div>
  );
}

function AccessModeControl({
  value,
  onChange,
  disabled,
}: {
  value: WebuiDefaultAccessMode;
  onChange: (value: WebuiDefaultAccessMode) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const options: Array<{ value: WebuiDefaultAccessMode; label: string }> = [
    { value: "default", label: tx("settings.values.defaultPermission", "Default Permission") },
    { value: "full", label: tx("settings.values.fullAccess", "Full Access") },
  ];
  return (
    <div
      role="radiogroup"
      className="inline-flex max-w-full flex-wrap items-center gap-0.5 rounded-full bg-muted p-0.5 text-[12px] font-medium text-muted-foreground"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          disabled={disabled}
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded-full px-3 py-1 transition-colors",
            value === option.value && "bg-background text-foreground shadow-sm",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function DevGuardrailsSecurity({
  settings,
  onSettingsChange,
  onOpenSettings,
}: {
  settings: SettingsPayload | null;
  /** The gateway answers every save with the whole payload; share it. */
  onSettingsChange: (payload: SettingsPayload) => void;
  /** Settings > Security: where the Restart button lives. */
  onOpenSettings: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { token } = useClient();
  const isNativeHost = (settings?.surface ?? settings?.runtime_surface) === "native";

  // Web safety: edited locally, written on Save (one restart-gated write).
  const [form, setForm] = useState<NetworkSafetySettingsUpdate>(() => networkSafetyForm(settings));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = networkSafetyDirty(form, settings);
  // The payload the form was last derived from: the panel mounts before the
  // settings arrive, so "untouched" is judged against it, not the fresh one.
  const formBase = useRef<SettingsPayload | null>(settings);
  useEffect(() => {
    const previous = formBase.current;
    formBase.current = settings;
    // A fresh payload (load, another card's save) resets an untouched form.
    setForm((prev) => nextNetworkSafetyForm(prev, previous, settings));
  }, [settings]);

  const save = async () => {
    if (!token || !dirty || saving) return;
    setSaving(true);
    try {
      const payload = await updateNetworkSafetySettings(token, form);
      onSettingsChange(payload);
      setForm(networkSafetyForm(payload));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // Agent browser: each switch writes straight away, like in Settings.
  const [browserSaving, setBrowserSaving] = useState(false);
  const headless = settings?.browser?.headless ?? true;
  const liveView = settings?.browser?.live_view ?? true;
  const persistBrowser = async (update: { browserHeadless?: boolean; browserLiveView?: boolean }) => {
    if (!token) return;
    setBrowserSaving(true);
    try {
      const payload = await updateSettings(token, update);
      onSettingsChange(payload);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBrowserSaving(false);
    }
  };

  const restartPending = runtimeRestartPending(settings) && !dirty;
  const status = dirty
    ? t("settings.status.unsaved")
    : restartPending
      ? tx("settings.status.savedRestartApply", "Saved. Restart when ready.")
      : null;

  return (
    <div className="space-y-3" data-testid="dev-guardrails-security">
      {error ? (
        <p className="rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
          {error}
        </p>
      ) : null}

      <SecurityCard
        icon={Network}
        title={
          isNativeHost
            ? tx("settings.sections.hostSafety", "App safety")
            : tx("settings.sections.webuiSafety", "Web safety")
        }
        testId="dev-guardrails-web-safety"
      >
        <SecurityRow
          title={tx("settings.rows.localServiceAccess", "Local Service Access")}
          description={tx(
            isNativeHost ? "settings.help.localServiceAccessNative" : "settings.help.localServiceAccess",
            isNativeHost
              ? "Allow Full Access shell commands to reach services on this Mac."
              : "Allow Full Access shell commands to reach localhost services.",
          )}
        >
          <ToggleButton
            checked={form.webuiAllowLocalServiceAccess}
            disabled={saving || !settings}
            onChange={(webuiAllowLocalServiceAccess) =>
              setForm((prev) => ({ ...prev, webuiAllowLocalServiceAccess }))
            }
            ariaLabel={tx("settings.rows.localServiceAccess", "Local Service Access")}
            label={
              form.webuiAllowLocalServiceAccess
                ? tx("settings.values.on", "On")
                : tx("settings.values.off", "Off")
            }
          />
        </SecurityRow>
        <SecurityRow
          title={tx("settings.rows.webuiDefaultAccess", "Default access")}
          description={tx(
            isNativeHost ? "settings.help.webuiDefaultAccessNative" : "settings.help.webuiDefaultAccess",
            isNativeHost
              ? "This is a different setting from Restrict to workspace.\nDefault Permission: the chat stays in the chosen folder, with confirmation for risky actions.\nFull Access: the agent can go anywhere on this Mac. Click Save. This mostly applies to new chats. A chat already open keeps its previous mode."
              : "This is a different setting from Restrict to workspace.\nDefault Permission: the chat stays in the chosen folder, with confirmation for risky actions.\nFull Access: the agent can go anywhere on this machine. Click Save. This mostly applies to new chats. A chat already open keeps its previous mode.",
          )}
        >
          <AccessModeControl
            value={form.webuiDefaultAccessMode}
            disabled={saving || !settings}
            onChange={(webuiDefaultAccessMode) =>
              setForm((prev) => ({ ...prev, webuiDefaultAccessMode }))
            }
          />
        </SecurityRow>
        <div className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p
            className={cn(
              "min-h-5 text-[12px] leading-5",
              dirty || restartPending ? "text-foreground/80" : "text-muted-foreground",
            )}
            data-testid="dev-guardrails-web-safety-status"
          >
            {status}
          </p>
          <div className="flex shrink-0 flex-wrap justify-end gap-2">
            {restartPending ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 rounded-full px-2.5 text-[12px]"
                onClick={onOpenSettings}
                data-testid="dev-guardrails-restart-link"
              >
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                {tx("dev.guardrails.restartFromSettings", "Restart from Settings")}
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 rounded-full px-3 text-[12px]"
              onClick={() => void save()}
              disabled={!dirty || saving}
              data-testid="dev-guardrails-web-safety-save"
            >
              {saving ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : null}
              {saving ? t("settings.actions.saving") : t("settings.actions.save")}
            </Button>
          </div>
        </div>
      </SecurityCard>

      <div className="space-y-2" data-testid="dev-guardrails-exec-policy">
        <div className="flex items-center gap-2 px-1 pt-1">
          <TerminalSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="text-[12.5px] font-semibold text-foreground">
            {tx("settings.sections.agentPermissions", "Agent permissions")}
          </span>
        </div>
        <ExecPolicySettings />
      </div>

      <SecurityCard
        icon={Globe}
        title={tx("settings.sections.browser", "Agent browser")}
        testId="dev-guardrails-browser"
      >
        <SecurityRow
          title={tx("settings.rows.browserWindow", "Show a real window")}
          description={tx(
            "settings.help.browserWindow",
            "The browser runs hidden by default. Show it when a site refuses automated traffic and a captcha has to be solved by hand. Applies to the next browser the agent opens: close the current one from the Agent browser panel to switch straight away.",
          )}
        >
          <ToggleButton
            checked={!headless}
            disabled={browserSaving || !settings}
            onChange={(next) => void persistBrowser({ browserHeadless: !next })}
            label={tx("settings.rows.browserWindow", "Show a real window")}
          />
        </SecurityRow>
        <SecurityRow
          title={tx("settings.rows.browserLiveView", "Mirror it in the editor")}
          description={tx(
            "settings.help.browserLiveView",
            "Stream what the browser is doing into the Agent browser panel, where you can also take control of the page.",
          )}
        >
          <ToggleButton
            checked={liveView}
            disabled={browserSaving || !settings}
            onChange={(next) => void persistBrowser({ browserLiveView: next })}
            label={tx("settings.rows.browserLiveView", "Mirror it in the editor")}
          />
        </SecurityRow>
      </SecurityCard>

      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <p className="text-[11.5px] leading-5 text-muted-foreground">
          {tx(
            "settings.help.securityManagedControls",
            "Web fetches always protect local, private, and metadata services. Core channel safety stays in config.json.",
          )}
        </p>
        <button
          type="button"
          onClick={onOpenSettings}
          className="inline-flex items-center gap-1 text-[11.5px] font-medium text-foreground/80 underline-offset-2 hover:text-foreground hover:underline"
          data-testid="dev-guardrails-open-settings"
        >
          {tx("dev.guardrails.openSettings", "Open Settings > Security")}
          <ExternalLink className="h-3 w-3" aria-hidden />
        </button>
      </div>
    </div>
  );
}
