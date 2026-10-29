// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Loader2,
  Plus,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ToggleButton } from "@/components/settings/ToggleButton";
import {
  type ChannelProviderPreset,
  type ChannelSetupPresentation,
} from "@/components/settings/channels/catalog";
import {
  CredentialForm,
  channelValuesForSubmit,
  defaultChannelFieldValues,
} from "@/components/settings/channels/CredentialForm";
import {
  ChannelLogo,
  ChannelStatusBadge,
  channelDescription,
  channelDisplayName,
  channelRequirements,
  channelSetup,
  channelStatusLabel,
} from "@/components/settings/channels/ChannelIdentity";
import {
  ChannelProviderPresets,
  ChannelSetupActions,
  ChannelSetupLinks,
  ChannelSetupSteps,
  ChannelValidationBadge,
  ChannelValidationChecks,
  ChannelValidationDetails,
} from "@/components/settings/channels/ChannelSetupParts";
import { SocialOauthSetup } from "@/components/settings/channels/SocialOauthSetup";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  configureChannel,
  validateChannel,
  startChannelLogin,
  fetchChannelLoginStatus,
  cancelChannelLogin,
} from "@/lib/api";
import type {
  ChannelLoginPayload,
  ChannelValidationPayload,
  NavinFeatureInfo,
  NavinFeaturesPayload,
} from "@/lib/types";

export function ChannelSetupPanel({
  token,
  feature,
  actionKey,
  docsBaseUrl,
  showBrandLogos,
  onAction,
  onFeaturesUpdate,
  variant = "panel",
  onClose,
}: {
  token: string;
  feature: NavinFeatureInfo;
  actionKey: string | null;
  docsBaseUrl?: string;
  showBrandLogos: boolean;
  onAction: (action: "enable" | "disable", name: string) => void;
  onFeaturesUpdate: (payload: NavinFeaturesPayload) => void;
  /**
   * panel: a bordered card. dialog: a fixed header over a scrolling body.
   * embedded: the toggle and the setup only, inside the dialog of the app
   * card the channel shares (that dialog shows the name and the logo).
   */
  variant?: "panel" | "dialog" | "embedded";
  onClose?: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const enableBusy = actionKey === `enable:${feature.name}`;
  const disableBusy = actionKey === `disable:${feature.name}`;
  const missingSupport = !feature.installed;
  const requiredWebui = feature.name === "websocket";
  const channelChecked = requiredWebui || feature.enabled;
  const channelBusy = enableBusy || disableBusy;
  const setup = channelSetup(feature);
  const needsSetupBeforeEnable =
    !channelChecked
    && feature.configured === false
;
  const channelToggleDisabled =
    requiredWebui
    || channelBusy
    || needsSetupBeforeEnable
    || (!feature.install_supported && !feature.installed && !feature.enabled);
  const installSupportLabel = tx("settings.navinFeatures.installSupport", "Install support");
  const toggleAriaLabel = t("settings.channels.toggleChannel", {
    name: channelDisplayName(feature),
    defaultValue: "{{name}} tool",
  });

  const installButton =
    missingSupport && feature.install_supported ? (
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={enableBusy}
        onClick={() => onAction("enable", feature.name)}
        className="mt-2 h-8 rounded-full px-3 text-[12px] font-semibold"
      >
        {enableBusy ? (
          <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Plus className="me-1.5 h-3.5 w-3.5" aria-hidden />
        )}
        {installSupportLabel}
      </Button>
    ) : null;
  const identity = (
    <div className="flex min-w-0 items-start gap-3">
      <ChannelLogo feature={feature} showBrandLogos={showBrandLogos} />
      <div className="min-w-0 flex-1">
        <h3 className="truncate text-[18px] font-semibold leading-6 text-foreground">
          {channelDisplayName(feature)}
        </h3>
        <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
          {channelDescription(feature, t)}
        </p>
        {installButton}
      </div>
    </div>
  );
  const controls = (
    <div className="flex shrink-0 items-center gap-2 pt-1">
      <ChannelStatusBadge>{channelStatusLabel(feature, tx)}</ChannelStatusBadge>
      {channelBusy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
      ) : null}
      <ToggleButton
        checked={channelChecked}
        disabled={channelToggleDisabled}
        ariaLabel={toggleAriaLabel}
        label={channelChecked ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
        onChange={(checked) => {
          onAction(checked ? "enable" : "disable", feature.name);
        }}
      />
      {onClose ? (
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-9 w-9 shrink-0 rounded-full"
          onClick={onClose}
          aria-label={tx("settings.connectors.close", "Close")}
          data-testid="channel-detail-close"
        >
          <X className="h-4 w-4" aria-hidden />
        </Button>
      ) : null}
    </div>
  );
  const surface = (
    <ChannelSetupSurface
      token={token}
      feature={feature}
      setup={setup}
      docsBaseUrl={docsBaseUrl}
      onFeaturesUpdate={onFeaturesUpdate}
    />
  );

  if (variant === "embedded") {
    return (
      <div data-testid={`channel-setup-${feature.name}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[12.5px] leading-5 text-muted-foreground">{channelDescription(feature, t)}</p>
            {installButton}
          </div>
          {controls}
        </div>
        {surface}
      </div>
    );
  }
  if (variant === "dialog") {
    return (
      <div className="flex min-h-0 flex-1 flex-col" data-testid={`channel-setup-${feature.name}`}>
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border/60 px-5 pb-4 pt-5">
          {identity}
          {controls}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-5" data-testid="channel-detail-body">
          {surface}
        </div>
      </div>
    );
  }
  return (
    <aside className="min-h-full rounded-[20px] border border-border/80 bg-background p-5 shadow-none">
      <div className="flex items-start justify-between gap-4">
        {identity}
        {controls}
      </div>
      {surface}
    </aside>
  );
}

function ChannelSetupSurface({
  token,
  feature,
  setup,
  docsBaseUrl,
  onFeaturesUpdate,
}: {
  token: string;
  feature: NavinFeatureInfo;
  setup: ChannelSetupPresentation;
  docsBaseUrl?: string;
  onFeaturesUpdate: (payload: NavinFeaturesPayload) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<ChannelValidationPayload | null>(null);
  const [visibleSecrets, setVisibleSecrets] = useState<Record<string, boolean>>({});
  const [touchedFields, setTouchedFields] = useState<Set<string>>(() => new Set());
  const configValuesKey = JSON.stringify(feature.config_values ?? {});
  const configuredFields = useMemo(
    () => new Set(feature.configured_fields ?? []),
    [feature.configured_fields],
  );
  const mode = setup.mode ?? "credentials";
  const fields = setup.fields ?? [];
  const requiredFields = fields.filter((field) => !field.optional);
  const primaryFields = requiredFields.length ? requiredFields : fields.slice(0, 1);
  const optionalFields = fields.filter((field) => field.optional);
  const manualFields = setup.manualFields ?? [];
  const advancedFields = mode === "connect" ? manualFields : optionalFields;
  const editableFields = mode === "credentials" ? fields : mode === "connect" ? manualFields : [];
  const hasAdvanced = advancedFields.length > 0;
  const requirements = channelRequirements(feature, t);
  const summary = t(`settings.channels.items.${feature.name}.setup.summary`, {
    defaultValue:
      setup.summary ??
      tx(
        "settings.channels.setupSummary",
        "Enable only turns on navin support. Add the platform credentials, then restart navin.",
      ),
  });
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() =>
    defaultChannelFieldValues(editableFields, feature.config_values),
  );
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loginStatus, setLoginStatus] = useState<ChannelLoginPayload | null>(null);
  const linkedRef = useRef(false);

  useEffect(() => {
    setNotice(null);
    setVisibleSecrets({});
    setSaving(false);
    setValidating(false);
    setValidation(null);
    setTouchedFields(new Set());
    setFieldValues(defaultChannelFieldValues(editableFields, feature.config_values));
  }, [configValuesKey, feature.name]);

  useEffect(() => {
    if (!loginOpen) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await fetchChannelLoginStatus(token, feature.name);
        if (cancelled) return;
        setLoginStatus(payload);
        if (payload.status === "waiting_qr" || payload.status === "starting") {
          setLoginBusy(true);
        }
        if (payload.status === "connected") {
          setLoginBusy(false);
          if (!linkedRef.current) {
            linkedRef.current = true;
            setNotice(tx("settings.channels.whatsappLinked", "WhatsApp is linked. Enabling the channel."));
            try {
              const enabled = await configureChannel(token, feature.name, {}, { enable: true });
              if (enabled.navin_features) onFeaturesUpdate(enabled.navin_features);
            } catch (err) {
              setLoginError((err as Error).message);
            }
          }
        }
        if (payload.status === "failed" || payload.status === "cancelled") {
          setLoginBusy(false);
          if (payload.error) setLoginError(payload.error);
        }
      } catch (err) {
        if (!cancelled) {
          setLoginError((err as Error).message);
          setLoginBusy(false);
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 900);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [loginOpen, token, feature.name, onFeaturesUpdate, tx]);

  const toggleSecret = (key: string) => {
    setVisibleSecrets((current) => ({ ...current, [key]: !current[key] }));
  };

  const setFieldValue = (key: string, value: string) => {
    setFieldValues((current) => ({ ...current, [key]: value }));
    setTouchedFields((current) => new Set(current).add(key));
  };

  const applyPreset = (preset: ChannelProviderPreset) => {
    setFieldValues((current) => ({ ...current, ...preset.values }));
    setTouchedFields((current) => {
      const next = new Set(current);
      for (const key of Object.keys(preset.values)) next.add(key);
      return next;
    });
  };

  const startInAppLogin = async (force = false) => {
    linkedRef.current = false;
    setLoginOpen(true);
    setLoginBusy(true);
    setLoginError(null);
    try {
      const started = await startChannelLogin(token, feature.name, { force });
      setLoginStatus(started);
    } catch (err) {
      setLoginError((err as Error).message);
      setLoginBusy(false);
    }
  };

  const saveCredentialSettings = async () => {
    setSaving(true);
    setValidating(true);
    setNotice(null);
    const values = channelValuesForSubmit(fields, fieldValues, touchedFields);
    try {
      const validationPayload = await validateChannel(token, feature.name, values);
      setValidation(validationPayload);
      if (!validationPayload.can_enable) {
        setNotice(
          validationPayload.message
            ?? tx("settings.channels.validationFailed", "Check the required setup before enabling."),
        );
        return;
      }
      const payload = await configureChannel(
        token,
        feature.name,
        values,
        { enable: true },
      );
      if (payload.navin_features) {
        onFeaturesUpdate(payload.navin_features);
      }
      setNotice(tx("settings.channels.checkedAndEnabled", "Checked and enabled."));
    } catch (err) {
      setNotice((err as Error).message);
    } finally {
      setSaving(false);
      setValidating(false);
    }
  };

  const checkCurrentSettings = async () => {
    setValidating(true);
    setNotice(null);
    try {
      const payload = await validateChannel(
        token,
        feature.name,
        channelValuesForSubmit(fields, fieldValues, touchedFields),
      );
      setValidation(payload);
      if (payload.message) setNotice(payload.message);
    } catch (err) {
      setNotice((err as Error).message);
    } finally {
      setValidating(false);
    }
  };

  const primaryActionLabel = feature.enabled
    ? tx("settings.channels.checkConnection", "Check connection")
    : tx("settings.channels.checkAndEnable", "Check and enable");

  return (
    <div className="mt-5 overflow-hidden rounded-[16px] border border-border/70 bg-background shadow-none">
      <section className="px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[13px] font-semibold text-foreground">
            {tx("settings.channels.requiredSetup", "Required setup")}
          </div>
          <div className="flex max-w-full flex-wrap justify-end gap-2">
            {mode !== "webui" ? (
              <ChannelValidationBadge
                validation={validation}
                validating={validating}
                feature={feature}
              />
            ) : null}
            {mode === "webui" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2.5 py-1 text-[11.5px] font-medium text-emerald-700 dark:text-emerald-200">
                <Check className="h-3.5 w-3.5" aria-hidden />
                {tx("settings.channels.managedByWebui", "Managed by WebUI")}
              </span>
            ) : null}
          </div>
        </div>
        <p className="mt-1 text-[12.5px] leading-5 text-muted-foreground">{requirements}</p>

        {/* Sign in or enter the keys first; the guide and its links come after. */}
        {mode === "oauth" ? (
          <SocialOauthSetup
            token={token}
            feature={feature}
            setup={setup}
            onFeaturesUpdate={onFeaturesUpdate}
          />
        ) : mode === "connect" ? (
          <>
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 rounded-full border-border/65 bg-background/80 px-3 text-[12px] font-semibold hover:bg-muted/70"
                onClick={() => void startInAppLogin(false)}
                disabled={loginBusy}
              >
                {loginBusy ? (
                  <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : null}
                {t(`settings.channels.items.${feature.name}.setup.primaryAction`, {
                  defaultValue: setup.primaryActionLabel ?? tx("settings.channels.connect", "Connect"),
                })}
              </Button>
            </div>
            <Dialog
              open={loginOpen}
              onOpenChange={(next) => {
                setLoginOpen(next);
                if (!next) void cancelChannelLogin(token, feature.name);
              }}
            >
              <DialogContent className="max-h-[min(90dvh,44rem)] max-w-md overflow-y-auto overscroll-contain">
                <DialogHeader>
                  <DialogTitle>{tx("settings.channels.whatsappQrTitle", "Link WhatsApp")}</DialogTitle>
                  <DialogDescription>
                    {tx(
                      "settings.channels.whatsappQrHint",
                      "On your phone: WhatsApp > Settings > Linked devices > Link a device. Scan this QR.",
                    )}
                  </DialogDescription>
                </DialogHeader>
                <div className="flex min-h-48 items-center justify-center rounded-[16px] border border-border/60 bg-background p-4">
                  {loginStatus?.qrDataUrl ? (
                    <img
                      src={loginStatus.qrDataUrl}
                      alt={tx("settings.channels.whatsappQrAlt", "WhatsApp QR code")}
                      className="h-56 w-56 rounded-md bg-white p-2"
                    />
                  ) : (
                    <div className="flex flex-col items-center gap-2 text-[13px] text-muted-foreground">
                      <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
                      {tx("settings.channels.whatsappQrWait", "Waiting for the QR...")}
                    </div>
                  )}
                </div>
                {loginStatus?.status === "connected" ? (
                  <p className="text-[13px] font-medium text-emerald-700 dark:text-emerald-200">
                    {tx("settings.channels.whatsappQrDone", "Linked. You can close this and send a test message.")}
                  </p>
                ) : null}
                {loginError ? (
                  <p className="text-[13px] text-destructive">{loginError}</p>
                ) : null}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-8 rounded-full px-3 text-[12px] font-semibold"
                  onClick={() => void startInAppLogin(true)}
                  disabled={loginBusy}
                >
                  {tx("settings.channels.whatsappQrAgain", "Show a new QR")}
                </Button>
              </DialogContent>
            </Dialog>
          </>
        ) : mode === "credentials" ? (
          <>
            {setup.presets?.length ? (
              <ChannelProviderPresets
                featureName={feature.name}
                presets={setup.presets}
                onApply={applyPreset}
              />
            ) : null}
            {primaryFields.length ? (
              <CredentialForm
                fields={primaryFields}
                values={fieldValues}
                configuredFields={configuredFields}
                visibleSecrets={visibleSecrets}
                onChange={setFieldValue}
                onToggleSecret={toggleSecret}
              />
            ) : null}
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 rounded-full border-border/65 bg-background/80 px-3 text-[12px] font-semibold hover:bg-muted/70"
                onClick={() => void saveCredentialSettings()}
                disabled={saving}
              >
                {saving || validating ? (
                  <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : null}
                {primaryActionLabel}
              </Button>
              {feature.configured || validation ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 rounded-full px-3 text-[12px] font-semibold"
                  onClick={() => void checkCurrentSettings()}
                  disabled={saving || validating}
                >
                  {tx("settings.channels.checkOnly", "Check only")}
                </Button>
              ) : null}
            </div>
          </>
        ) : null}
      </section>

      <section className="border-t border-border/60 px-4 py-4" data-testid="channel-setup-help">
        <p className="text-[12.5px] leading-5 text-muted-foreground">{summary}</p>
        <ChannelValidationDetails validation={validation} />
        <ChannelSetupLinks
          feature={feature}
          setup={setup}
          docsBaseUrl={docsBaseUrl}
          token={token}
        />
        <ChannelSetupActions feature={feature} setup={setup} onNotice={setNotice} />
      </section>

      {notice ? (
        <div
          role="status"
          className="border-t border-border/60 px-4 py-3 text-[12px] leading-5 text-muted-foreground"
        >
          {notice}
        </div>
      ) : null}

      {setup.steps.length ? (
        <ChannelSetupSteps featureName={feature.name} steps={setup.steps} tryIt={setup.tryIt} />
      ) : null}

      {validation?.checks.length ? (
        <ChannelValidationChecks validation={validation} token={token} />
      ) : null}

      {hasAdvanced ? (
        <details className="group border-t border-border/60 px-4 py-3 text-[12px] leading-5 text-muted-foreground">
          <summary className="cursor-pointer list-none text-[12px] font-semibold text-foreground">
            <span className="inline-flex items-center gap-1.5">
              {tx("settings.channels.advanced", "Advanced")}
              <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" aria-hidden />
            </span>
          </summary>
          {advancedFields.length ? (
            <div className="mt-3">
              <CredentialForm
                fields={advancedFields}
                values={fieldValues}
                configuredFields={configuredFields}
                visibleSecrets={visibleSecrets}
                onChange={setFieldValue}
                onToggleSecret={toggleSecret}
                compact
              />
            </div>
          ) : null}
        </details>
      ) : null}
    </div>
  );
}
