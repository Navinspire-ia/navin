// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import {
  Loader2,
  Package,
  PackagePlus,
  Plug,
  Power,
  Trash2,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { InstallSkillDialog } from "@/components/settings/InstallSkillDialog";
import { MigrationSettings } from "@/components/settings/MigrationSettings";
import { Button } from "@/components/ui/button";
import {
  fetchPluginPacks,
  removePluginPack,
  setPluginPackEnabled,
} from "@/lib/api";
import { readLastDevContext } from "@/lib/last-dev-context";
import { notifySkillsChanged } from "@/lib/skill-events";
import type { PluginPackSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

export function PluginPacksSettings({
  onPluginsChange,
  installOpen: installOpenProp,
  onInstallOpenChange,
  hideInstallButton = false,
}: {
  onPluginsChange?: () => void | Promise<void>;
  installOpen?: boolean;
  onInstallOpenChange?: (open: boolean) => void;
  hideInstallButton?: boolean;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const [plugins, setPlugins] = useState<PluginPackSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [installOpenLocal, setInstallOpenLocal] = useState(false);
  const installOpen = installOpenProp ?? installOpenLocal;
  const setInstallOpen = onInstallOpenChange ?? setInstallOpenLocal;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Pack awaiting removal confirmation (in-app dialog, never window.confirm:
  // native dialogs do not exist in the packaged desktop builds).
  const [removalTarget, setRemovalTarget] = useState<PluginPackSummary | null>(null);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const payload = await fetchPluginPacks(token);
      setPlugins(payload.plugins);
    } catch {
      // keep previous list
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const afterChange = useCallback(
    async (payload: { plugins: PluginPackSummary[] }) => {
      setPlugins(payload.plugins);
      notifySkillsChanged();
      await onPluginsChange?.();
    },
    [onPluginsChange],
  );

  const toggle = async (plugin: PluginPackSummary) => {
    if (!token) return;
    setBusy(plugin.name);
    setError(null);
    try {
      await afterChange(await setPluginPackEnabled(token, plugin.name, !plugin.enabled));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (plugin: PluginPackSummary) => {
    if (!token) return;
    setRemovalTarget(null);
    setBusy(plugin.name);
    setError(null);
    try {
      await afterChange(await removePluginPack(token, plugin.name));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="rounded-2xl border border-border/50 bg-card/70 px-4 py-4 sm:px-5">
      <ConfirmDialog
        open={removalTarget !== null}
        title={t("settings.plugins.removeTitle", {
          name: removalTarget?.name ?? "",
          defaultValue: "Remove “{{name}}”?",
        })}
        description={t("settings.plugins.removeConfirm", {
          name: removalTarget?.name ?? "",
          defaultValue:
            "Remove installed skill “{{name}}”? Its skills and MCP servers will be unregistered.",
        })}
        confirmLabel={t("settings.plugins.remove", { defaultValue: "Remove" })}
        onCancel={() => setRemovalTarget(null)}
        onConfirm={() => {
          if (removalTarget) void remove(removalTarget);
        }}
      />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-teal-500/15 text-teal-600 dark:text-teal-400">
            <Package className="h-4.5 w-4.5" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="text-[14px] font-semibold tracking-[-0.01em] text-foreground">
              {t("settings.plugins.title", { defaultValue: "Installed skills" })}
            </p>
            <p className="mt-0.5 max-w-[44rem] text-[12.5px] leading-5 text-muted-foreground">
              {t("settings.plugins.description", {
                defaultValue:
                  "Skill packs and MCP servers installed from Git, npm (npx) or a local folder. Skills load instantly, MCP servers hot-reload.",
              })}
            </p>
          </div>
        </div>
        {hideInstallButton ? null : (
          <Button
            type="button"
            variant="outline"
            className="h-9 shrink-0 rounded-xl px-3.5"
            onClick={() => setInstallOpen(true)}
          >
            <PackagePlus className="mr-1.5 h-4 w-4" aria-hidden />
            {t("settings.plugins.install", { defaultValue: "Install skill" })}
          </Button>
        )}
      </div>

      {error ? (
        <p className="mt-3 rounded-xl bg-destructive/10 px-3 py-2 text-[12.5px] text-destructive">
          {error}
        </p>
      ) : null}

      <div className="mt-3">
        <MigrationSettings />
      </div>

      <div className="mt-3">
        {loading ? (
          <div className="flex items-center gap-2 px-1 py-3 text-[13px] text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("settings.plugins.loading", { defaultValue: "Loading installed skills…" })}
          </div>
        ) : plugins.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/60 px-3 py-5 text-center text-[13px] text-muted-foreground">
            {t("settings.plugins.empty", {
              defaultValue:
                "No skills installed yet. A pack ships skills/<name>/SKILL.md and an optional mcp.json.",
            })}
          </div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {plugins.map((plugin) => (
              <div
                key={plugin.name}
                className={cn(
                  "flex flex-col gap-2 rounded-xl border border-border/50 bg-background/60 px-3.5 py-3",
                  !plugin.enabled && "opacity-60",
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-[13.5px] font-semibold text-foreground">
                      {plugin.display_name || plugin.name}
                      {plugin.version ? (
                        <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">
                          v{plugin.version}
                        </span>
                      ) : null}
                    </p>
                    {plugin.description ? (
                      <p className="mt-0.5 line-clamp-2 text-[12px] leading-5 text-muted-foreground">
                        {plugin.description}
                      </p>
                    ) : null}
                  </div>
                  <span
                    className={cn(
                      "shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium",
                      plugin.enabled
                        ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                        : "bg-muted text-muted-foreground",
                    )}
                  >
                    {plugin.enabled
                      ? t("settings.plugins.enabled", { defaultValue: "Enabled" })
                      : t("settings.plugins.disabled", { defaultValue: "Disabled" })}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <Wrench className="h-3 w-3" aria-hidden />
                    {t("settings.plugins.skillsCount", {
                      count: plugin.skills.length,
                      defaultValue: "{{count}} skill(s)",
                    })}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Plug className="h-3 w-3" aria-hidden />
                    {t("settings.plugins.mcpCount", {
                      count: plugin.mcp_servers.length,
                      defaultValue: "{{count}} MCP server(s)",
                    })}
                  </span>
                </div>
                <div className="mt-auto flex items-center gap-1.5">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-8 rounded-lg px-2.5 text-[12px]"
                    disabled={busy === plugin.name}
                    onClick={() => void toggle(plugin)}
                  >
                    {busy === plugin.name ? (
                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Power className="mr-1 h-3.5 w-3.5" aria-hidden />
                    )}
                    {plugin.enabled
                      ? t("settings.plugins.disable", { defaultValue: "Disable" })
                      : t("settings.plugins.enable", { defaultValue: "Enable" })}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-8 rounded-lg px-2.5 text-[12px] text-destructive hover:text-destructive"
                    disabled={busy === plugin.name}
                    onClick={() => setRemovalTarget(plugin)}
                  >
                    <Trash2 className="mr-1 h-3.5 w-3.5" aria-hidden />
                    {t("settings.plugins.remove", { defaultValue: "Remove" })}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <InstallSkillDialog
        open={installOpen}
        onOpenChange={setInstallOpen}
        projectPath={readLastDevContext()?.projectPath}
        onInstalled={async (payload) => {
          await afterChange(payload);
        }}
      />
    </section>
  );
}
