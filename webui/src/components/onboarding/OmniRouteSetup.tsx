// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  Loader2,
  Play,
  RefreshCw,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { fetchOmniRouteSetup, runOmniRouteSetupAction } from "@/lib/api";
import { copyTextOrNotify } from "@/lib/clipboard";
import type { OmniRouteSetupPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

// Slow poll picks up a gateway the user starts from their own terminal; the
// fast one moves the progress line while install/start run on the backend.
const POLL_MS = 3000;
const POLL_FAST_MS = 1000;

type Busy = "" | "refresh" | "install" | "start" | "configure";

export type OmniRouteSetupState = {
  status: OmniRouteSetupPayload | null;
  busy: Busy;
  error: string;
  /** Navin points at the gateway (endpoint + auto preset written). */
  done: boolean;
  /** Configured and the gateway answers: chat works right now. */
  ready: boolean;
  refresh: () => void;
  install: () => void;
  start: () => void;
  configure: () => void;
};

/**
 * Drives the OmniRoute path of the wizard: detects the local gateway (also
 * one started by hand or in Docker), installs/starts it through the backend
 * when possible, and pins Navin on the keyless ``auto`` combo.
 *
 * `onSynced` fires once the config was written so the IDE can refresh its
 * providers/models without a reload.
 */
export function useOmniRouteSetup(
  active: boolean,
  token?: string,
  onSynced?: () => void,
): OmniRouteSetupState {
  const { t } = useTranslation();
  const [status, setStatus] = useState<OmniRouteSetupPayload | null>(null);
  const [busy, setBusy] = useState<Busy>("");
  const [error, setError] = useState("");
  const onSyncedRef = useRef(onSynced);
  onSyncedRef.current = onSynced;

  // Success is conveyed by the localized step rows; only failures surface
  // the backend text (it carries the actual npm / process error).
  const apply = useCallback((payload: OmniRouteSetupPayload) => {
    setStatus(payload);
    if (payload.last_action && !payload.last_action.ok) {
      setError(payload.last_action.message);
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const payload = await fetchOmniRouteSetup(token);
      setStatus((prev) =>
        prev && busy && busy !== "refresh"
          ? { ...prev, ...payload, last_action: prev.last_action }
          : payload,
      );
    } catch {
      // Gateway briefly unreachable: keep the last known state.
    }
  }, [busy, token]);

  const running = Boolean(status?.running);
  const configured = Boolean(status?.configured);

  useEffect(() => {
    if (!active || !token) return;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      await refresh();
    };
    void tick();
    // Nothing left to detect once the gateway answers and Navin is wired.
    if (running && configured && !busy) return;
    const interval = busy && busy !== "refresh" ? POLL_FAST_MS : POLL_MS;
    const id = window.setInterval(() => void tick(), interval);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [active, busy, configured, refresh, running, token]);

  const runAction = useCallback(
    async (action: Exclude<Busy, "" | "refresh">) => {
      if (!token) return;
      setBusy(action);
      setError("");
      try {
        const payload = await runOmniRouteSetupAction(
          token,
          action,
          action === "configure" ? { makeActive: true } : {},
        );
        apply(payload);
        if (action === "configure" && payload.configured) onSyncedRef.current?.();
      } catch (err) {
        setError(
          (err as Error).message || t("onboarding.omniroute.error"),
        );
      } finally {
        setBusy("");
      }
    },
    [apply, t, token],
  );

  return {
    status,
    busy,
    error,
    done: configured,
    ready: configured && running,
    refresh: () => void refresh(),
    install: () => void runAction("install"),
    start: () => void runAction("start"),
    configure: () => void runAction("configure"),
  };
}

function CommandLine({ command, copyLabel, copiedLabel }: {
  command: string;
  copyLabel: string;
  copiedLabel: string;
}) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(id);
  }, [copied]);
  return (
    <div className="flex items-center gap-2">
      <code className="min-w-0 flex-1 overflow-x-auto rounded-lg bg-muted/60 px-2.5 py-1.5 text-[11px] text-foreground/90">
        {command}
      </code>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="h-7 shrink-0 gap-1 px-2 text-[11px]"
        aria-label={copyLabel}
        onClick={() => {
          void copyTextOrNotify(command).then((ok) => setCopied(ok));
        }}
      >
        {copied ? (
          <Check className="size-3.5 text-primary" aria-hidden />
        ) : (
          <Copy className="size-3.5" aria-hidden />
        )}
        {copied ? copiedLabel : copyLabel}
      </Button>
    </div>
  );
}

function StepRow({
  title,
  body,
  done,
  dimmed,
  action,
  children,
}: {
  title: string;
  body: string;
  done: boolean;
  dimmed?: boolean;
  action?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border px-4 py-3",
        done ? "border-primary/60 bg-primary/5" : "border-border",
        dimmed && !done ? "opacity-60" : "",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">{title}</div>
          <div className="mt-1 text-xs text-muted-foreground">{body}</div>
        </div>
        {done ? <Check className="size-4 shrink-0 text-primary" aria-hidden /> : action}
      </div>
      {children ? <div className="mt-3 grid gap-2">{children}</div> : null}
    </div>
  );
}

/** The three guided rows: install, start, use in Navin. */
export function OmniRouteSetupSteps({ state }: { state: OmniRouteSetupState }) {
  const { t } = useTranslation();
  const { status, busy, error, install, start, configure, refresh } = state;
  const installed = Boolean(status?.installed);
  const running = Boolean(status?.running);
  const configured = Boolean(status?.configured);
  const plan = status?.install;
  const nodeMissing = Boolean(plan && !plan.node_version);
  const nodeTooOld = plan?.node_supported === false;
  const job = status?.active_job ?? null;
  const copyLabel = t("onboarding.omniroute.copy");
  const copiedLabel = t("onboarding.omniroute.copied");

  if (!status) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        {t("onboarding.omniroute.detecting")}
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      <StepRow
        title={t("onboarding.omniroute.stepInstall")}
        body={
          installed
            ? t("onboarding.omniroute.stepInstallDone")
            : nodeMissing
              ? t("onboarding.omniroute.nodeMissing", { requirement: plan?.node_requirement })
              : nodeTooOld
                ? t("onboarding.omniroute.nodeTooOld", {
                    version: plan?.node_version,
                    requirement: plan?.node_requirement,
                  })
                : t("onboarding.omniroute.stepInstallBody")
        }
        done={installed}
        action={
          plan?.supported ? (
            <Button type="button" size="sm" disabled={busy !== ""} onClick={install}>
              {busy === "install" ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
              ) : (
                <Download className="size-3.5" aria-hidden />
              )}
              {busy === "install"
                ? t("onboarding.omniroute.installing")
                : t("onboarding.omniroute.install")}
            </Button>
          ) : (
            <Button type="button" size="sm" variant="outline" asChild>
              <a href={plan?.node_download_url} target="_blank" rel="noreferrer">
                <ExternalLink className="size-3.5" aria-hidden />
                {t("onboarding.omniroute.getNode")}
              </a>
            </Button>
          )
        }
      >
        {!installed ? (
          <CommandLine
            command={plan?.command ?? "npm install -g omniroute"}
            copyLabel={copyLabel}
            copiedLabel={copiedLabel}
          />
        ) : null}
        {busy === "install" ? (
          <p className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            {job?.message ?? t("onboarding.omniroute.installWait")}
          </p>
        ) : null}
      </StepRow>

      <StepRow
        title={t("onboarding.omniroute.stepStart")}
        body={
          running
            ? t("onboarding.omniroute.stepStartDone")
            : t("onboarding.omniroute.stepStartBody")
        }
        done={running}
        dimmed={!installed}
        action={
          <Button
            type="button"
            size="sm"
            disabled={busy !== "" || !installed}
            onClick={start}
          >
            {busy === "start" ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Play className="size-3.5" aria-hidden />
            )}
            {busy === "start"
              ? t("onboarding.omniroute.starting")
              : t("onboarding.omniroute.start")}
          </Button>
        }
      >
        {!running ? (
          <CommandLine
            command={plan?.start_command ?? "omniroute"}
            copyLabel={copyLabel}
            copiedLabel={copiedLabel}
          />
        ) : null}
        {!running && busy !== "start" ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 self-start text-[11px] text-muted-foreground underline-offset-2 hover:underline"
            onClick={refresh}
          >
            <RefreshCw className="size-3" aria-hidden />
            {t("onboarding.omniroute.checkAgain")}
          </button>
        ) : null}
      </StepRow>

      <StepRow
        title={t("onboarding.omniroute.stepUse")}
        body={
          configured
            ? running
              ? t("onboarding.omniroute.stepUseDone", { count: status.model_count })
              : t("onboarding.omniroute.stepUseDoneStopped")
            : running
              ? t("onboarding.omniroute.stepUseBody", { count: status.model_count })
              : t("onboarding.omniroute.stepUseWaiting")
        }
        done={configured}
        dimmed={!running}
        action={
          <Button
            type="button"
            size="sm"
            disabled={busy !== "" || !running}
            onClick={configure}
          >
            {busy === "configure" ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : null}
            {t("onboarding.omniroute.use")}
          </Button>
        }
      />

      <p className="text-xs text-muted-foreground">
        {t("onboarding.omniroute.hint")}
        {running ? (
          <>
            {" "}
            <a
              href={status.dashboard_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
            >
              {t("onboarding.omniroute.dashboard")}
              <ExternalLink className="size-3" aria-hidden />
            </a>
          </>
        ) : null}
      </p>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
