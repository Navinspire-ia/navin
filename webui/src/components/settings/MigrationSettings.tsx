// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { ArrowDownToLine, Check, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { MigrationImportPayload, MigrationScanPayload } from "@/lib/api";
import { runMigrationImport, scanMigrationSources } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

/**
 * "Import from Cursor" card: detects Cursor MCP servers (global + project)
 * and Copilot instructions, then imports them into Navin in one click.
 * Cursor rules (.cursor/rules, .cursorrules) need no import: Navin reads
 * them natively, so they are listed as already supported.
 */
export function MigrationSettings() {
  const { token } = useClient();
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [scan, setScan] = useState<MigrationScanPayload | null>(null);
  const [scanning, setScanning] = useState(false);
  const [importing, setImporting] = useState(false);
  const [report, setReport] = useState<MigrationImportPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!token) return;
    setScanning(true);
    setError(null);
    try {
      setScan(await scanMigrationSources(token));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setScanning(false);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleImport = useCallback(async () => {
    if (!token) return;
    setImporting(true);
    setError(null);
    setReport(null);
    try {
      const result = await runMigrationImport(token);
      setReport(result);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setImporting(false);
    }
  }, [token, refresh]);

  const mcpServerCount = (scan?.mcp ?? []).reduce(
    (total, source) => total + source.servers.length,
    0,
  );
  const hasImportable =
    mcpServerCount > 0 || Boolean(scan?.copilotInstructions);

  return (
    <div className="rounded-xl border border-border/60 bg-background/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[13px] font-semibold text-foreground">
            {tx("settings.migration.title", "Import from Cursor / VS Code")}
          </h3>
          <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
            {tx(
              "settings.migration.blurb",
              "Brings your Cursor MCP servers and Copilot instructions into Navin. Cursor rules (.cursor/rules, .cursorrules) are already read natively - nothing to convert.",
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 rounded-lg px-2"
            onClick={() => void refresh()}
            disabled={scanning}
            aria-label={tx("settings.migration.rescan", "Rescan")}
          >
            <RefreshCw
              className={scanning ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"}
              aria-hidden
            />
          </Button>
          <Button
            type="button"
            size="sm"
            className="h-8 rounded-lg"
            onClick={() => void handleImport()}
            disabled={importing || !hasImportable}
          >
            {importing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <ArrowDownToLine className="h-3.5 w-3.5" aria-hidden />
            )}
            {tx("settings.migration.import", "Import")}
          </Button>
        </div>
      </div>

      {error ? (
        <p className="mt-2 text-[12px] text-destructive">{error}</p>
      ) : null}

      {scan ? (
        <div className="mt-3 space-y-1.5 text-[12px]">
          {scan.mcp.map((source) => (
            <p key={source.path} className="text-muted-foreground">
              <span className="font-medium text-foreground">
                {source.origin === "global"
                  ? tx("settings.migration.globalMcp", "Cursor MCP (global)")
                  : tx("settings.migration.projectMcp", "Cursor MCP (project)")}
              </span>
              {": "}
              {source.servers.join(", ")}
            </p>
          ))}
          {scan.copilotInstructions ? (
            <p className="text-muted-foreground">
              <span className="font-medium text-foreground">
                {tx("settings.migration.copilot", "Copilot instructions")}
              </span>
              {": "}
              {tx("settings.migration.copilotHint", "will become a Navin rule")}
            </p>
          ) : null}
          {scan.nativeRules.length > 0 ? (
            <p className="text-muted-foreground">
              <span className="font-medium text-foreground">
                {tx("settings.migration.rules", "Cursor rules")}
              </span>
              {": "}
              {scan.nativeRules.join(", ")}{" "}
              <span className="text-emerald-600">
                {tx(
                  "settings.migration.rulesNative",
                  "(already supported natively)",
                )}
              </span>
            </p>
          ) : null}
          {!hasImportable && scan.nativeRules.length === 0 ? (
            <p className="text-muted-foreground">
              {tx(
                "settings.migration.nothing",
                "Nothing to import was detected on this machine.",
              )}
            </p>
          ) : null}
        </div>
      ) : null}

      {report ? (
        <div className="mt-3 rounded-lg bg-muted/50 px-3 py-2 text-[12px]">
          <p className="flex items-center gap-1.5 font-medium text-emerald-600">
            <Check className="h-3.5 w-3.5" aria-hidden />
            {tx("settings.migration.done", "Import finished")}
          </p>
          <p className="mt-0.5 text-muted-foreground">
            {tx("settings.migration.mcpImported", "MCP servers imported")}
            {": "}
            {report.mcp.imported.length > 0
              ? report.mcp.imported.join(", ")
              : tx("settings.migration.none", "none")}
            {report.mcp.skipped.length > 0
              ? ` · ${tx("settings.migration.mcpSkipped", "already present")}: ${report.mcp.skipped.join(", ")}`
              : ""}
          </p>
          {report.copilot.imported ? (
            <p className="text-muted-foreground">
              {tx(
                "settings.migration.copilotDone",
                "Copilot instructions imported as a Navin rule.",
              )}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
