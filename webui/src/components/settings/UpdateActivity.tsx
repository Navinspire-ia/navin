// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { fetchUpdateHistory, type UpdateHistoryEntry, type UpdateStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const EVENT_LABELS: Record<string, [string, string]> = {
  checked: ["settings.updates.events.checked", "Checked"],
  downloaded: ["settings.updates.events.downloaded", "Downloaded"],
  installing: ["settings.updates.events.installing", "Installing"],
  installed: ["settings.updates.events.installed", "Installed"],
  rollback: ["settings.updates.events.rollback", "Rolled back"],
  error: ["settings.updates.events.error", "Failed"],
};

const ENGINE_LABELS: Record<string, [string, string]> = {
  tauri: ["settings.updates.engineTauri", "Desktop updater"],
  navin: ["settings.updates.engineNavin", "Navin updater"],
  manual: ["settings.updates.engineManual", "Manual"],
};

type Loaded = { history: UpdateHistoryEntry[]; status: UpdateStatus | null };

/**
 * Settings > About: what the automatic updates did on this machine - checks,
 * downloads, installs, rollbacks and errors, newest first - and which engine
 * installs this format. Read from the gateway's local history.
 */
export function UpdateActivity({ token, limit = 8 }: { token: string; limit?: number }) {
  const { t, i18n } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [data, setData] = useState<Loaded | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchUpdateHistory(token, limit);
      setData({ history: payload.history ?? [], status: payload.status ?? null });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [limit, token]);

  useEffect(() => {
    void load();
  }, [load]);

  const engineKey = data?.status?.strategy?.engine;
  const engine = engineKey ? ENGINE_LABELS[engineKey] : undefined;
  const formatter = new Intl.DateTimeFormat(i18n.language || undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });

  return (
    <div className="space-y-2 px-4 py-3.5 sm:px-5" data-testid="update-activity">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[14px] font-medium leading-5 text-foreground">
            {tx("settings.updates.historyTitle", "Update activity")}
          </div>
          {engine ? (
            <div className="text-[12px] leading-5 text-muted-foreground" data-testid="update-engine">
              {t("settings.updates.installedBy", {
                defaultValue: "Installed by: {{engine}}",
                engine: tx(engine[0], engine[1]),
              })}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          aria-label={tx("settings.updates.refresh", "Refresh")}
          title={tx("settings.updates.refresh", "Refresh")}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted/65 hover:text-foreground disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </div>
      {error ? (
        <p className="text-[12px] text-destructive" role="alert">
          {tx("settings.updates.historyError", "Could not load the update activity.")} {error}
        </p>
      ) : null}
      {!error && !loading && data && data.history.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          {tx("settings.updates.historyEmpty", "No update activity yet.")}
        </p>
      ) : null}
      {data && data.history.length > 0 ? (
        <ul className="space-y-1" data-testid="update-history">
          {data.history.map((entry, index) => {
            const label = EVENT_LABELS[entry.event];
            const failed = !entry.ok || entry.event === "error";
            const when = Number.isFinite(entry.at) ? formatter.format(new Date(entry.at * 1000)) : "";
            return (
              <li
                key={`${entry.at}-${entry.event}-${index}`}
                data-event={entry.event}
                className="flex items-start gap-2 text-[12px] leading-5"
              >
                {failed ? (
                  <AlertCircle className="mt-1 h-3 w-3 shrink-0 text-destructive" aria-hidden />
                ) : (
                  <Check className="mt-1 h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                )}
                <span className="min-w-0 flex-1">
                  <span className={cn("font-medium", failed ? "text-destructive" : "text-foreground")}>
                    {label ? tx(label[0], label[1]) : entry.event}
                  </span>
                  {entry.version ? <span className="text-muted-foreground"> v{entry.version}</span> : null}
                  {entry.detail ? (
                    <span className="block break-words text-muted-foreground">{entry.detail}</span>
                  ) : null}
                </span>
                <span className="shrink-0 tabular-nums text-muted-foreground">{when}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
