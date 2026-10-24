// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, ExternalLink, Github, Loader2, RotateCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { openInOsBrowser, pollGitDeviceFlow, startGitDeviceFlow } from "@/lib/api";
import { copyTextToClipboard } from "@/lib/clipboard";

/** GitHub asks for 5 more seconds between polls on every `slow_down`. */
const SLOW_DOWN_STEP_S = 5;
const MIN_INTERVAL_S = 1;

export type DeviceFlowState =
  | { phase: "idle" }
  | { phase: "starting" }
  | {
      phase: "waiting";
      flowId: string;
      userCode: string;
      verificationUri: string;
      intervalS: number;
      expiresAt: number;
    }
  | { phase: "ok"; login: string | null }
  | { phase: "expired" }
  | { phase: "denied" }
  | { phase: "error"; message: string };

/**
 * "Sign in with GitHub" without a browser redirect back to the app: GitHub's
 * device flow shows a short code, the user types it on github.com, and this
 * polls until GitHub says yes, no, or the code ran out.
 */
export function GitDeviceFlow({
  token,
  host = "github.com",
  onConnected,
}: {
  token: string | null | undefined;
  host?: string;
  onConnected?: (login: string | null) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  const [state, setState] = useState<DeviceFlowState>({ phase: "idle" });
  const [copied, setCopied] = useState(false);
  // Bumped on cancel/restart/unmount so a poll in flight cannot land late.
  const runRef = useRef(0);
  const onConnectedRef = useRef(onConnected);
  onConnectedRef.current = onConnected;

  useEffect(() => () => {
    runRef.current += 1;
  }, []);

  const start = useCallback(async () => {
    if (!token) return;
    const run = ++runRef.current;
    setCopied(false);
    setState({ phase: "starting" });
    try {
      const flow = await startGitDeviceFlow(token, host);
      if (run !== runRef.current) return;
      setState({
        phase: "waiting",
        flowId: flow.flow_id,
        userCode: flow.user_code,
        verificationUri: flow.verification_uri,
        intervalS: Math.max(MIN_INTERVAL_S, Number(flow.interval) || 5),
        expiresAt: Date.now() + Math.max(1, Number(flow.expires_in) || 900) * 1000,
      });
      void openInOsBrowser(token, flow.verification_uri);
    } catch (err) {
      if (run !== runRef.current) return;
      setState({ phase: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }, [host, token]);

  const cancel = useCallback(() => {
    runRef.current += 1;
    setState({ phase: "idle" });
  }, []);

  // One timer per waiting step: pending keeps the interval, slow_down widens it.
  const waitingKey =
    state.phase === "waiting" ? `${state.flowId}:${state.intervalS}:${state.expiresAt}` : null;
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (state.phase !== "waiting" || !token) return;
    const run = runRef.current;
    const { flowId, intervalS, expiresAt } = state;
    const timer = window.setTimeout(async () => {
      if (run !== runRef.current) return;
      if (Date.now() >= expiresAt) {
        setState({ phase: "expired" });
        return;
      }
      try {
        const result = await pollGitDeviceFlow(token, flowId);
        if (run !== runRef.current) return;
        switch (result.status) {
          case "pending":
            setTick((value) => value + 1);
            return;
          case "slow_down":
            setState((prev) =>
              prev.phase === "waiting" && prev.flowId === flowId
                ? { ...prev, intervalS: prev.intervalS + SLOW_DOWN_STEP_S }
                : prev,
            );
            return;
          case "ok":
            setState({ phase: "ok", login: result.login ?? null });
            onConnectedRef.current?.(result.login ?? null);
            return;
          case "expired":
            setState({ phase: "expired" });
            return;
          case "denied":
            setState({ phase: "denied" });
            return;
          default:
            setState({
              phase: "error",
              message: result.error || tx("settings.git.deviceError", "GitHub sign-in failed."),
            });
        }
      } catch (err) {
        if (run !== runRef.current) return;
        setState({ phase: "error", message: err instanceof Error ? err.message : String(err) });
      }
    }, intervalS * 1000);
    return () => window.clearTimeout(timer);
    // `tick` re-arms the same step after a pending answer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waitingKey, tick, token]);

  const copyCode = async (code: string) => {
    const ok = await copyTextToClipboard(code);
    setCopied(ok);
  };

  if (state.phase === "idle" || state.phase === "starting") {
    return (
      <Button
        type="button"
        onClick={() => void start()}
        disabled={!token || state.phase === "starting"}
        className="h-9 rounded-full text-[13px]"
      >
        {state.phase === "starting" ? (
          <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Github className="me-1.5 h-3.5 w-3.5" aria-hidden />
        )}
        {tx("settings.git.signInGithub", "Sign in with GitHub")}
      </Button>
    );
  }

  if (state.phase === "waiting") {
    return (
      <div className="flex flex-col gap-2.5" data-testid="git-device-waiting">
        <p className="text-[12px] leading-5 text-muted-foreground">
          {tx(
            "settings.git.deviceInstructions",
            "Enter this code on GitHub. The page opened in your browser; this updates by itself once you approve.",
          )}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <span
            dir="ltr"
            className="select-all rounded-xl border border-border/55 bg-muted/50 px-4 py-2 font-mono text-[24px] font-semibold tracking-[0.18em] text-foreground"
            data-testid="git-device-code"
          >
            {state.userCode}
          </span>
          <Button
            type="button"
            variant="outline"
            onClick={() => void copyCode(state.userCode)}
            className="h-9 rounded-full text-[13px]"
          >
            {copied ? (
              <Check className="me-1 h-3.5 w-3.5" aria-hidden />
            ) : (
              <Copy className="me-1 h-3.5 w-3.5" aria-hidden />
            )}
            {copied
              ? tx("settings.git.copied", "Copied")
              : tx("settings.git.copyCode", "Copy code")}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => token && void openInOsBrowser(token, state.verificationUri)}
            className="h-9 rounded-full text-[13px]"
          >
            <ExternalLink className="me-1 h-3.5 w-3.5" aria-hidden />
            {tx("settings.git.openGithub", "Open GitHub")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={cancel}
            className="h-9 rounded-full text-[13px] text-muted-foreground"
          >
            <X className="me-1 h-3.5 w-3.5" aria-hidden />
            {tx("settings.git.cancel", "Cancel")}
          </Button>
        </div>
        <p className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          {tx("settings.git.deviceWaiting", "Waiting for approval on GitHub...")}
        </p>
      </div>
    );
  }

  if (state.phase === "ok") {
    return (
      <div className="flex flex-wrap items-center gap-2" data-testid="git-device-ok">
        <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-emerald-700 dark:text-emerald-300">
          <Check className="h-4 w-4" aria-hidden />
          {state.login
            ? tx("settings.git.deviceOk", "Signed in as {{login}}", { login: state.login })
            : tx("settings.git.deviceOkNoLogin", "Signed in to GitHub")}
        </span>
        <Button
          type="button"
          variant="ghost"
          onClick={() => setState({ phase: "idle" })}
          className="h-8 rounded-full text-[12px] text-muted-foreground"
        >
          {tx("settings.git.done", "Done")}
        </Button>
      </div>
    );
  }

  const message =
    state.phase === "expired"
      ? tx("settings.git.deviceExpired", "The code expired before it was approved.")
      : state.phase === "denied"
        ? tx("settings.git.deviceDenied", "Access was denied on GitHub.")
        : state.message;
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid={`git-device-${state.phase}`}>
      <span role="alert" className="text-[12px] font-medium text-destructive">
        {message}
      </span>
      <Button
        type="button"
        variant="outline"
        onClick={() => void start()}
        className="h-8 rounded-full text-[12px]"
      >
        <RotateCw className="me-1 h-3.5 w-3.5" aria-hidden />
        {tx("settings.git.tryAgain", "Try again")}
      </Button>
    </div>
  );
}
