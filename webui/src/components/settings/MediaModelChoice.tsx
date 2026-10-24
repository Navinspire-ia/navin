// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { KeyRound, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { fetchProviderModels } from "@/lib/api";
import type { MediaModelChoices, MediaModelOption, MediaProviderRef, MediaUseKind } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

/** The Live realtime engine only bridges these providers. */
const LIVE_PROVIDERS = new Set(["navin", "openai", "gemini", "bedrock", "xai", "qwen"]);
const CACHE_MS = 5 * 60_000;
const cache = new Map<string, { at: number; rows: Promise<MediaModelOption[]> }>();

function providerModels(token: string, ref: MediaProviderRef, kind: MediaUseKind): Promise<MediaModelOption[]> {
  const key = `${ref.provider}:${kind}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.rows;
  const rows = fetchProviderModels(token, ref.provider, "", { modality: kind })
    .then((result) => (result.status === "available" ? result.models : []).map((row) => ({
      value: `${ref.provider}::${row.id}`,
      provider: ref.provider,
      provider_label: ref.label,
      model: row.id,
      label: row.label || row.id,
      ready: true,
    })))
    .catch(() => {
      cache.delete(key);
      return [] as MediaModelOption[];
    });
  cache.set(key, { at: Date.now(), rows });
  return rows;
}

/**
 * One model per use (image, video, listening, voice, Live), listed straight
 * from the connected providers (Navin's plan, Gemini, OpenAI...). Picking a
 * model sets its provider too.
 */
export function MediaModelChoice({
  kind,
  choices,
  providers,
  value: rawValue,
  onChange,
  onOpenProviders,
  allowNone = false,
  className,
}: {
  kind: MediaUseKind;
  choices: MediaModelChoices | undefined;
  /** Connected providers (settings payload `media_providers`). */
  providers: MediaProviderRef[] | undefined;
  /** "provider::model" */
  value: string;
  onChange: (next: { provider: string; model: string; value: string }) => void;
  onOpenProviders?: () => void;
  allowNone?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const tx = (key: string, fallback: string) => t(`settings.mediaModels.${key}`, { defaultValue: fallback });
  const sources = useMemo(
    () => (providers ?? []).filter((ref) => kind !== "live" || LIVE_PROVIDERS.has(ref.provider)),
    [kind, providers],
  );
  const sourceKey = sources.map((ref) => ref.provider).join(",");
  const [fetched, setFetched] = useState<MediaModelOption[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let current = true;
    if (!sources.length) {
      setFetched([]);
      return;
    }
    setLoading(true);
    void Promise.all(sources.map((ref) => providerModels(token, ref, kind))).then((lists) => {
      if (!current) return;
      setFetched(lists.flat());
      setLoading(false);
    });
    return () => { current = false; };
    // sourceKey stands for `sources`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, sourceKey, token]);

  const options = useMemo(() => {
    const seen = new Set<string>();
    const merged: MediaModelOption[] = [];
    for (const option of [...(choices?.[kind]?.options ?? []), ...fetched]) {
      if (seen.has(option.value)) continue;
      seen.add(option.value);
      merged.push(option);
    }
    return merged;
  }, [choices, fetched, kind]);
  const groups = useMemo(() => {
    const byProvider = new Map<string, MediaModelOption[]>();
    for (const option of options) {
      const list = byProvider.get(option.provider_label) ?? [];
      list.push(option);
      byProvider.set(option.provider_label, list);
    }
    return [...byProvider.entries()].sort(([a], [b]) => (a === "Navin" ? -1 : b === "Navin" ? 1 : a.localeCompare(b)));
  }, [options]);
  // Gemini ids are stored with or without "models/": one model, one entry.
  const value = rawValue.replace(/^gemini::models\//, "gemini::");
  const current = options.find((option) => option.value === value);

  if (!options.length) {
    return (
      <div className={cn("flex flex-col items-end gap-1.5 text-end", className)}>
        {loading ? (
          <p className="flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {tx("loading", "Loading the models of your providers...")}
          </p>
        ) : (
          <>
            <p className="text-[12.5px] text-muted-foreground">
              {sources.length
                ? tx(kind === "live" ? "emptyLive" : "emptyConnected", "None of your connected providers offers a model for this use.")
                : tx("noProvider", "Connect a provider (its API key) to list its models here.")}
            </p>
            {onOpenProviders ? (
              <Button size="sm" variant="outline" className="rounded-full" onClick={onOpenProviders}>
                {tx("connectProvider", "Connect a provider")}
              </Button>
            ) : null}
          </>
        )}
      </div>
    );
  }

  return (
    <div className={cn("flex w-[min(340px,75vw)] flex-col items-end gap-1.5", className)}>
      <select
        aria-label={tx(`label.${kind}`, "Model")}
        value={current ? value : ""}
        onChange={(event) => {
          const next = event.target.value;
          if (!next) {
            onChange({ provider: "", model: "", value: "" });
            return;
          }
          const option = options.find((item) => item.value === next);
          if (option) onChange({ provider: option.provider, model: option.model, value: option.value });
        }}
        className="h-9 w-full rounded-lg border border-border bg-background px-2.5 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {!current || allowNone ? (
          <option value="">{allowNone ? tx("none", "None") : tx("choose", "Choose a model")}</option>
        ) : null}
        {groups.map(([provider, rows]) => (
          <optgroup key={provider} label={provider}>
            {rows.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label === option.model ? option.model : `${option.label} (${option.model})`}
                {option.ready ? "" : ` · ${tx("noKey", "key missing")}`}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {loading ? (
        <p className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          {tx("loadingMore", "Loading more models...")}
        </p>
      ) : current && !current.ready ? (
        <div className="flex flex-wrap items-center justify-end gap-2">
          <p className="flex items-center gap-1.5 text-[11.5px] text-amber-700 dark:text-amber-300">
            <KeyRound className="h-3.5 w-3.5" aria-hidden />
            {tx("needsKey", "This provider has no key yet.")}
          </p>
          {onOpenProviders ? (
            <Button size="sm" variant="outline" className="h-7 rounded-full text-[12px]" onClick={onOpenProviders}>
              {tx("addKey", "Add the key")}
            </Button>
          ) : null}
        </div>
      ) : current ? (
        <p className="text-[11.5px] text-muted-foreground">{current.provider_label}</p>
      ) : null}
    </div>
  );
}
