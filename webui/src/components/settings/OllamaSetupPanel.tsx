import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  Download,
  ExternalLink,
  Loader2,
  Play,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchOllamaSetup, runOllamaSetupAction } from "@/lib/api";
import type { OllamaSetupPayload, SettingsPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

type BusyAction = "refresh" | "install" | "start" | "pull" | "configure" | null;

function formatElapsed(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(safe / 60);
  const secs = safe % 60;
  return mins > 0 ? `${mins}:${String(secs).padStart(2, "0")}` : `${secs}s`;
}

export function OllamaSetupPanel({
  token,
  onConfigured,
}: {
  token: string;
  onConfigured: (settings: SettingsPayload) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });

  const [status, setStatus] = useState<OllamaSetupPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [busyStartedAt, setBusyStartedAt] = useState<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [customModel, setCustomModel] = useState("");
  const [activeModel, setActiveModel] = useState<string | null>(null);

  const applyPayload = useCallback(
    (payload: OllamaSetupPayload) => {
      setStatus(payload);
      setMessage(payload.last_action?.message ?? null);
      if (payload.settings) onConfigured(payload.settings);
    },
    [onConfigured],
  );

  const refresh = useCallback(async () => {
    setBusy("refresh");
    setError(null);
    try {
      applyPayload(await fetchOllamaSetup(token));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
      setBusyStartedAt(null);
    }
  }, [applyPayload, token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Live elapsed timer while an action is running.
  useEffect(() => {
    if (!busy || busy === "refresh" || busyStartedAt == null) {
      setElapsedSec(0);
      return;
    }
    const tick = () => setElapsedSec(Math.floor((Date.now() - busyStartedAt) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [busy, busyStartedAt]);

  // Poll status during long install/pull so the progress bar can move.
  useEffect(() => {
    if (busy !== "install" && busy !== "pull" && busy !== "start") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await fetchOllamaSetup(token);
        if (cancelled) return;
        setStatus((prev) => ({
          ...(prev ?? payload),
          ...payload,
          last_action: prev?.last_action,
        }));
      } catch {
        // Ignore poll errors while the main action request is in flight.
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [busy, token]);

  const runAction = async (
    action: Exclude<BusyAction, "refresh" | null>,
    options: { model?: string; makeActive?: boolean } = {},
  ) => {
    setBusy(action);
    setBusyStartedAt(Date.now());
    setActiveModel(options.model ?? null);
    setError(null);
    setMessage(null);
    try {
      applyPayload(await runOllamaSetupAction(token, action, options));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
      setBusyStartedAt(null);
      setActiveModel(null);
    }
  };

  const phase = status?.phase ?? "install";
  const installSupported = status?.install.supported ?? false;
  const loading = status === null && busy === "refresh";
  const showInstall = !!status && !status.installed && !status.running;
  const activeJob = status?.active_job ?? null;
  const progressPct =
    typeof activeJob?.progress === "number" && Number.isFinite(activeJob.progress)
      ? Math.max(0, Math.min(100, activeJob.progress))
      : null;

  const progressTitle = useMemo(() => {
    if (busy === "install") {
      return tx("settings.ollamaSetup.progressInstall", "Installing Ollama...");
    }
    if (busy === "start") {
      return tx("settings.ollamaSetup.progressStart", "Starting Ollama...");
    }
    if (busy === "pull") {
      return tx("settings.ollamaSetup.progressPull", "Downloading model...");
    }
    if (busy === "configure") {
      return tx("settings.ollamaSetup.progressConfigure", "Configuring Navin...");
    }
    return null;
  }, [busy, t]);

  const platformLabel = useMemo(() => {
    switch (status?.platform) {
      case "windows":
        return "Windows";
      case "macos":
        return "macOS";
      case "wsl":
        return "WSL (Linux)";
      case "linux":
        return "Linux";
      default:
        return null;
    }
  }, [status?.platform]);

  return (
    <div className="space-y-3 rounded-[18px] border border-border/45 bg-background/80 px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
            <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden />
            {tx("settings.ollamaSetup.title", "Local Ollama setup")}
          </p>
          <p className="mt-1 text-[12px] leading-5 text-muted-foreground">
            {tx(
              "settings.ollamaSetup.description",
              "Detect the daemon, install if needed, pull a model, then wire Navin to localhost:11434.",
            )}
          </p>
          {platformLabel ? (
            <p className="mt-1 text-[11px] text-muted-foreground/90">
              {tx("settings.ollamaSetup.detectedOs", "Detected OS: {{os}}", {
                os: platformLabel,
              })}
            </p>
          ) : null}
        </div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => void refresh()}
          disabled={busy !== null}
          className="rounded-full"
          aria-label={tx("settings.ollamaSetup.refresh", "Refresh status")}
        >
          {busy === "refresh" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          )}
        </Button>
      </div>

      {loading ? (
        <p className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {tx("settings.ollamaSetup.detecting", "Detecting local Ollama...")}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <StatusChip
            ok={!!status?.installed}
            label={tx("settings.ollamaSetup.installed", "Installed")}
          />
          <StatusChip
            ok={!!status?.running}
            label={tx("settings.ollamaSetup.running", "Running")}
          />
          <StatusChip
            ok={!!status?.configured}
            label={tx("settings.ollamaSetup.configured", "Configured")}
          />
          <StatusChip
            ok={(status?.model_count ?? 0) > 0}
            label={tx("settings.ollamaSetup.modelsCount", "{{count}} models", {
              count: status?.model_count ?? 0,
            })}
          />
        </div>
      )}

      {status?.running && status.binary_available === false ? (
        <p className="rounded-[12px] border border-border/50 bg-muted/25 px-3 py-2 text-[12px] leading-5 text-muted-foreground">
          {tx(
            "settings.ollamaSetup.daemonWithoutBinary",
            "Ollama answers on port 11434, but the CLI was not found in PATH. Install steps are hidden - you can still pull models through the API.",
          )}
        </p>
      ) : null}

      {busy && busy !== "refresh" && progressTitle ? (
        <div
          className="space-y-2 rounded-[14px] border border-primary/25 bg-primary/5 px-3 py-3"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-start gap-2">
            <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-primary" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-[12.5px] font-semibold text-foreground">{progressTitle}</p>
              <p className="mt-0.5 text-[12px] text-muted-foreground">
                {activeJob?.message
                  || (busy === "pull" && activeModel
                    ? tx("settings.ollamaSetup.progressPullModel", "Pulling {{model}}...", {
                        model: activeModel,
                      })
                    : tx(
                        "settings.ollamaSetup.progressPleaseWait",
                        "Please wait - this can take several minutes.",
                      ))}
              </p>
            </div>
            <span className="shrink-0 tabular-nums text-[11px] font-medium text-muted-foreground">
              {formatElapsed(elapsedSec)}
              {progressPct != null ? ` · ${Math.round(progressPct)}%` : null}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted/70">
            {progressPct != null ? (
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
                style={{ width: `${progressPct}%` }}
              />
            ) : (
              <div className="h-full w-1/3 animate-pulse rounded-full bg-primary/70" />
            )}
          </div>
        </div>
      ) : null}

      {showInstall ? (
        <div className="space-y-2 rounded-[14px] border border-dashed border-border/60 bg-muted/20 px-3 py-3">
          <p className="text-[12px] font-medium text-foreground">
            {tx("settings.ollamaSetup.installTitle", "1. Install Ollama")}
          </p>
          <ol className="list-decimal space-y-1 pl-4 text-[12px] text-muted-foreground">
            {status.install.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          {status.install.command ? (
            <code className="block overflow-x-auto rounded-lg bg-muted/50 px-2.5 py-1.5 text-[11px] text-foreground/90">
              {status.install.command}
            </code>
          ) : null}
          <div className="flex flex-wrap gap-2 pt-1">
            {installSupported ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="rounded-full"
                disabled={busy !== null}
                onClick={() => void runAction("install")}
              >
                {busy === "install" ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                )}
                {busy === "install"
                  ? tx("settings.ollamaSetup.installing", "Installing...")
                  : tx("settings.ollamaSetup.install", "Install Ollama")}
              </Button>
            ) : null}
            <Button type="button" size="sm" variant="ghost" className="rounded-full" asChild>
              <a href={status.install.download_url} target="_blank" rel="noreferrer">
                <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                {tx("settings.ollamaSetup.downloadPage", "Download page")}
              </a>
            </Button>
          </div>
        </div>
      ) : null}

      {status?.installed && !status.running ? (
        <div className="space-y-2 rounded-[14px] border border-dashed border-border/60 bg-muted/20 px-3 py-3">
          <p className="text-[12px] font-medium text-foreground">
            {tx("settings.ollamaSetup.startTitle", "2. Start the local server")}
          </p>
          <p className="text-[12px] text-muted-foreground">
            {tx(
              "settings.ollamaSetup.startHelp",
              "Ollama is installed but nothing answers on port 11434 yet.",
            )}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="rounded-full"
            disabled={busy !== null}
            onClick={() => void runAction("start")}
          >
            {busy === "start" ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Play className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {busy === "start"
              ? tx("settings.ollamaSetup.starting", "Starting...")
              : tx("settings.ollamaSetup.start", "Start Ollama")}
          </Button>
        </div>
      ) : null}

      {status?.running ? (
        <div className="space-y-2">
          <p className="text-[12px] font-medium text-foreground">
            {tx("settings.ollamaSetup.pullTitle", "3. Pull a model")}
          </p>
          <div className="space-y-2">
            {status.recommended_models.map((model) => {
              const pulling = busy === "pull" && activeModel === model.id;
              const using = busy === "configure" && activeModel === model.id;
              return (
                <div
                  key={model.id}
                  className="flex flex-col gap-2 rounded-[14px] border border-border/40 bg-muted/15 px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="truncate text-[12.5px] font-medium text-foreground">
                      {model.label}
                      <span className="ml-1.5 font-normal text-muted-foreground">
                        {model.size_hint}
                      </span>
                    </p>
                    <p className="truncate text-[11.5px] text-muted-foreground">
                      {model.description}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    {model.installed ? (
                      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                        <Check className="h-3 w-3" aria-hidden />
                        {tx("settings.ollamaSetup.pulled", "Pulled")}
                      </span>
                    ) : null}
                    <Button
                      type="button"
                      size="sm"
                      variant={model.installed ? "ghost" : "outline"}
                      className="rounded-full"
                      disabled={busy !== null}
                      onClick={() => void runAction("pull", { model: model.id })}
                    >
                      {pulling ? (
                        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                      ) : (
                        <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                      )}
                      {pulling
                        ? tx("settings.ollamaSetup.pulling", "Pulling...")
                        : model.installed
                          ? tx("settings.ollamaSetup.repull", "Re-pull")
                          : tx("settings.ollamaSetup.pull", "Pull")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="rounded-full"
                      disabled={busy !== null || !model.installed}
                      onClick={() =>
                        void runAction("configure", { model: model.id, makeActive: true })
                      }
                    >
                      {using ? (
                        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                      ) : null}
                      {tx("settings.ollamaSetup.useModel", "Use")}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={customModel}
              onChange={(event) => setCustomModel(event.target.value)}
              placeholder={tx(
                "settings.ollamaSetup.customPlaceholder",
                "Or type any model id (e.g. llama3.2:latest)",
              )}
              className="h-9 rounded-full text-[13px]"
              disabled={busy !== null}
            />
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="rounded-full"
                disabled={busy !== null || !customModel.trim()}
                onClick={() => void runAction("pull", { model: customModel.trim() })}
              >
                {busy === "pull" && activeModel === customModel.trim() ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : null}
                {busy === "pull" && activeModel === customModel.trim()
                  ? tx("settings.ollamaSetup.pulling", "Pulling...")
                  : tx("settings.ollamaSetup.pull", "Pull")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="rounded-full"
                disabled={busy !== null || !customModel.trim()}
                onClick={() =>
                  void runAction("configure", {
                    model: customModel.trim(),
                    makeActive: true,
                  })
                }
              >
                {busy === "configure" && activeModel === customModel.trim() ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : null}
                {tx("settings.ollamaSetup.useModel", "Use")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {status?.running ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-border/40 pt-3">
          <Button
            type="button"
            size="sm"
            variant={status.configured ? "ghost" : "outline"}
            className="rounded-full"
            disabled={busy !== null}
            onClick={() => void runAction("configure", { makeActive: false })}
          >
            {busy === "configure" && !activeModel ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {status.configured
              ? tx("settings.ollamaSetup.reconfigure", "Refresh Navin config")
              : tx("settings.ollamaSetup.configure", "4. Configure Navin")}
          </Button>
          <span className="text-[11px] text-muted-foreground">
            {status.api_base || status.default_api_base}
          </span>
          {phase === "ready" ? (
            <span className="text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
              {tx("settings.ollamaSetup.ready", "Ready for chat")}
            </span>
          ) : null}
        </div>
      ) : null}

      {message ? <p className="text-[12px] text-muted-foreground">{message}</p> : null}
      {error ? <p className="text-[12px] text-destructive">{error}</p> : null}
    </div>
  );
}

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-medium",
        ok
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "bg-muted text-muted-foreground",
      )}
    >
      {label}
    </span>
  );
}
