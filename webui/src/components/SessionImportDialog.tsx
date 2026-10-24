// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { motion } from "framer-motion";
import { ChevronDown, Download, FolderOpen, Loader2, PackageOpen, Upload } from "lucide-react";

import {
  addImportRoot,
  importExternalSessions,
  openSessionBundle,
  removeImportRoot,
  uploadAndOpenSessionBundle,
  scanExternalSessions,
  type SessionImportSourceStat,
} from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FolderBrowserDialog } from "@/components/dev/DevProjectSelector";
import { nativeFolderPickerAvailable, pickNativeFile } from "@/lib/native-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type Phase = "idle" | "scanning" | "ready" | "importing" | "done" | "error";

/**
 * Fallback list used when a scan failed or timed out and returned no
 * sources: the custom-root dropdown must stay usable (issue #3) instead
 * of being stuck on the raw default "cursor" with no items.
 */
const KNOWN_SOURCES: Array<{ name: string; label: string }> = [
  { name: "claude-code", label: "Claude Code" },
  { name: "codex", label: "Codex" },
  { name: "opencode", label: "OpenCode" },
  { name: "oh-my-pi", label: "oh-my-pi" },
  { name: "cursor", label: "Cursor" },
];

function statusLabel(status: string): string {
  if (status === "ready") return "";
  if (status === "missing") return "not installed";
  return "unsupported";
}

/**
 * Import chats from external tools (claude-code, codex, opencode,
 * oh-my-pi, cursor) into the current workspace. Detection is automatic;
 * importing writes standard Navin sessions that appear in this sidebar.
 */
export function SessionImportDialog({
  open,
  onOpenChange,
  onImported,
  onOpenedShared,
  initialFile = null,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported?: () => void;
  /** A shared .navin-session was rebuilt: open its new chat. */
  onOpenedShared?: (chatId: string) => void;
  /** A .navin-session dropped on the sidebar list: opened as soon as the dialog shows. */
  initialFile?: File | null;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [phase, setPhase] = useState<Phase>("idle");
  const [sources, setSources] = useState<SessionImportSourceStat[]>([]);
  const [savedRoots, setSavedRoots] = useState<Record<string, string[]>>({});
  const [newRootSource, setNewRootSource] = useState("cursor");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [resultNote, setResultNote] = useState("");
  const [bundleBusy, setBundleBusy] = useState(false);
  const [bundlePath, setBundlePath] = useState("");
  const [bundleNote, setBundleNote] = useState("");
  const [bundleError, setBundleError] = useState("");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);

  // A .navin-session someone shared: rebuild its project and open the chat.
  // Either a path on this machine (desktop picker, pasted path) or a file the
  // browser uploads in slices.
  const openShared = useCallback(async (source: string | File) => {
    const clean = typeof source === "string" ? source.trim() : "";
    if (typeof source === "string" && !clean) return;
    if (typeof source !== "string" && !source.name.toLowerCase().endsWith(".navin-session")) {
      setBundleError(t("sidebar.sessionImport.sharedWrongType", {
        defaultValue: "Choose a .navin-session file.",
      }));
      return;
    }
    setBundleBusy(true);
    setBundleError("");
    setBundleNote("");
    setUploadProgress(typeof source === "string" ? null : 0);
    try {
      const opened = typeof source === "string"
        ? await openSessionBundle(token, clean)
        : await uploadAndOpenSessionBundle(token, source, (fraction) => setUploadProgress(fraction));
      setBundleNote(
        t("sidebar.sessionImport.sharedOpened", {
          defaultValue: "Opened in {{dir}}.",
          dir: opened.project_dir,
        })
        + (opened.notes.length ? ` ${opened.notes.join(" ")}` : "")
        + (opened.excluded_secrets.length
          ? ` ${t("sidebar.sessionImport.sharedSecrets", {
            defaultValue: "To recreate (not shared): {{list}}.",
            list: opened.excluded_secrets.slice(0, 6).join(", "),
          })}`
          : ""),
      );
      onImported?.();
      onOpenedShared?.(opened.chat_id);
    } catch (exc) {
      setBundleError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBundleBusy(false);
      setUploadProgress(null);
    }
  }, [onImported, onOpenedShared, t, token]);

  // A file dropped on the sidebar list: open it once, right away.
  const startedFileRef = useRef<File | null>(null);
  useEffect(() => {
    if (!open) {
      startedFileRef.current = null;
      return;
    }
    if (!initialFile || startedFileRef.current === initialFile) return;
    startedFileRef.current = initialFile;
    void openShared(initialFile);
  }, [initialFile, open, openShared]);

  const pickShared = useCallback(async () => {
    try {
      const picked = await pickNativeFile(["navin-session"], "Navin session");
      if (picked) await openShared(picked);
    } catch {
      // No desktop bridge: the path field below.
    }
  }, [openShared]);

  const scan = useCallback(async () => {
    setPhase("scanning");
    setError("");
    try {
      const payload = await scanExternalSessions(token);
      setSources(payload.sources);
      setSavedRoots(payload.saved_roots ?? {});
      // Default selection: every source that actually has sessions to import.
      setSelected(
        Object.fromEntries(
          payload.sources.map((s) => [s.name, (s.discovered ?? 0) > 0]),
        ),
      );
      setPhase("ready");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // A slow scan used to surface the generic "engine took too long"
      // transport copy; say what actually happened instead.
      setError(
        /too long|timeout|timed out/i.test(message)
          ? "Le scan des stores externes est lent (grands historiques). Les sources restent selectionnables ci-dessous - reessayez ou importez depuis une racine personnalisee."
          : message,
      );
      setPhase("error");
    }
  }, [token]);

  const addRoot = useCallback(
    async (path: string) => {
      const clean = path.trim();
      if (!clean) return;
      setError("");
      try {
        const payload = await addImportRoot(token, newRootSource, clean);
        setSavedRoots(payload.saved_roots ?? {});
        await scan();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [token, newRootSource, scan],
  );

  const removeRoot = useCallback(
    async (source: string, path: string) => {
      setError("");
      try {
        const payload = await removeImportRoot(token, source, path);
        setSavedRoots(payload.saved_roots ?? {});
        await scan();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [token, scan],
  );

  useEffect(() => {
    if (open) void scan();
  }, [open, scan]);

  const runImport = useCallback(async () => {
    setPhase("importing");
    setError("");
    try {
      const selectedNames = sources
        .filter((s) => selected[s.name])
        .map((s) => s.name);
      const allSelected =
        selectedNames.length === sources.length && sources.length > 0;
      const payload = await importExternalSessions(
        token,
        allSelected ? undefined : { source: selectedNames.join(",") },
      );
      const imported = payload.sources.reduce(
        (sum, s) => sum + (s.imported ?? 0),
        0,
      );
      const skipped = payload.sources.reduce(
        (sum, s) => sum + (s.skipped_existing ?? 0),
        0,
      );
      setResultNote(
        t("sidebar.sessionImport.result", {
          imported,
          skipped,
          defaultValue: `${imported} imported, ${skipped} already present`,
        }),
      );
      setSources(
        payload.sources.map((s) => ({
          ...s,
          discovered: s.imported ?? 0,
        })),
      );
      setPhase("done");
      onImported?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }, [token, sources, selected, t, onImported]);

  const busy = phase === "scanning" || phase === "importing";
  const dropdownSources = sources.length ? sources : KNOWN_SOURCES;
  const newRootLabel =
    sources.find((s) => s.name === newRootSource)?.label ??
    KNOWN_SOURCES.find((s) => s.name === newRootSource)?.label ??
    newRootSource;
  const selectedTotal = sources
    .filter((s) => selected[s.name])
    .reduce((sum, s) => sum + s.discovered, 0);
  const selectedCount = sources.filter((s) => selected[s.name]).length;

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90dvh] max-w-md flex-col overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {t("sidebar.sessionImport.title", {
              defaultValue: "Import external sessions",
            })}
          </DialogTitle>
          <DialogDescription>
            {t("sidebar.sessionImport.description", {
              defaultValue:
                "Detect chats from claude-code, codex, opencode, oh-my-pi and cursor installed on this machine, then import them into this workspace.",
            })}
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-64 space-y-1.5 overflow-y-auto">
          {busy ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {t("sidebar.sessionImport.scanning", {
                defaultValue: "Scanning this machine...",
              })}
            </div>
          ) : (
            sources.map((source, idx) => (
              <motion.div
                key={source.name}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.03, duration: 0.18 }}
                className="flex items-center justify-between rounded-md px-2 py-1.5 text-sm hover:bg-accent/50"
              >
                <label className="flex min-w-0 items-center gap-2">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 shrink-0 accent-[var(--primary)]"
                    checked={Boolean(selected[source.name])}
                    disabled={(source.discovered ?? 0) === 0}
                    onChange={(e) =>
                      setSelected((prev) => ({
                        ...prev,
                        [source.name]: e.target.checked,
                      }))
                    }
                    aria-label={source.label}
                  />
                  <span className="truncate">{source.label}</span>
                </label>
                <span className="shrink-0 text-muted-foreground">
                  {statusLabel(source.status) ||
                    t("sidebar.sessionImport.found", {
                      count: source.discovered,
                      defaultValue: "{{count}} found",
                    })}
                </span>
              </motion.div>
            ))
          )}
        </div>

        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
        {resultNote ? (
          <p className="text-sm text-muted-foreground">{resultNote}</p>
        ) : null}

        {!busy ? (
          <div className="space-y-2 rounded-md border p-2">
            <p className="text-xs font-medium text-muted-foreground">
              {t("sidebar.sessionImport.customRoots", {
                defaultValue:
                  "Chats on another machine? Mount or sync its folder (SMB, rsync, USB) and add its path here.",
              })}
            </p>
            {Object.entries(savedRoots).map(([source, roots]) =>
              roots.map((root) => (
                <div
                  key={`${source}:${root}`}
                  className="flex items-center justify-between gap-2 text-xs"
                >
                  <span className="truncate text-muted-foreground">
                    <span className="font-medium">{source}</span>: {root}
                  </span>
                  <button
                    type="button"
                    className="shrink-0 text-muted-foreground hover:text-destructive"
                    onClick={() => void removeRoot(source, root)}
                  >
                    {t("common.remove", { defaultValue: "Remove" })}
                  </button>
                </div>
              )),
            )}
            <div className="flex items-center gap-1.5">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 shrink-0 gap-1 px-2 text-xs"
                  >
                    {newRootLabel}
                    <ChevronDown className="h-3 w-3 opacity-60" aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start">
                  {dropdownSources.map((s) => (
                    <DropdownMenuItem
                      key={s.name}
                      onSelect={() => setNewRootSource(s.name)}
                    >
                      {s.label}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
              <Button
                variant="outline"
                size="sm"
                className="h-7 flex-1 justify-start gap-1.5 px-2 text-xs"
                onClick={() => setBrowserOpen(true)}
              >
                <FolderOpen className="h-3.5 w-3.5 shrink-0" aria-hidden />
                <span className="truncate">
                  {t("sidebar.sessionImport.browse", {
                    defaultValue: "Browse folders...",
                  })}
                </span>
              </Button>
            </div>
          </div>
        ) : null}

        <div className="rounded-lg border border-border/70 p-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <PackageOpen className="h-4 w-4 text-muted-foreground" aria-hidden />
            {t("sidebar.sessionImport.sharedTitle", { defaultValue: "Open a shared session (.navin-session)" })}
          </div>
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            {t("sidebar.sessionImport.sharedHint", {
              defaultValue: "Rebuilds the project (history and pending changes) in a new folder under Navin, then opens the conversation where it stopped.",
            })}
          </p>
          <div
            className={cn(
              "mt-2 rounded-md border border-dashed p-2 transition-colors",
              dragOver ? "border-primary bg-primary/5" : "border-border/70",
            )}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const file = e.dataTransfer.files?.[0];
              if (file && !bundleBusy) void openShared(file);
            }}
          >
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={bundleBusy} onClick={() => fileInput.current?.click()}>
                {uploadProgress !== null ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Upload className="h-4 w-4" aria-hidden />}
                {t("sidebar.sessionImport.sharedUpload", { defaultValue: "Upload a file" })}
              </Button>
              {nativeFolderPickerAvailable() ? (
                <Button size="sm" variant="outline" disabled={bundleBusy} onClick={() => void pickShared()}>
                  <FolderOpen className="h-4 w-4" aria-hidden />
                  {t("sidebar.sessionImport.sharedPick", { defaultValue: "Choose the file" })}
                </Button>
              ) : null}
            </div>
            <input
              ref={fileInput}
              type="file"
              accept=".navin-session"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) void openShared(file);
              }}
            />
            <p className="mt-1.5 text-[11.5px] text-muted-foreground">
              {t("sidebar.sessionImport.sharedDrop", { defaultValue: "or drop the file here, or paste its path:" })}
            </p>
            <form
              className="mt-1.5 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void openShared(bundlePath);
              }}
            >
              <input
                className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-xs"
                placeholder={t("sidebar.sessionImport.sharedPathPlaceholder", {
                  defaultValue: "/path/to/project.navin-session",
                })}
                value={bundlePath}
                onChange={(e) => setBundlePath(e.target.value)}
              />
              <Button size="sm" type="submit" variant="outline" disabled={bundleBusy || !bundlePath.trim()}>
                {t("sidebar.sessionImport.sharedOpen", { defaultValue: "Open" })}
              </Button>
            </form>
          </div>
          {uploadProgress !== null ? (
            <p className="mt-2 text-[12.5px] text-muted-foreground">
              {uploadProgress < 1
                ? t("sidebar.sessionImport.sharedUploading", {
                    defaultValue: "Uploading... {{percent}}%",
                    percent: Math.round(uploadProgress * 100),
                  })
                : t("sidebar.sessionImport.sharedBusy", { defaultValue: "Rebuilding the project..." })}
            </p>
          ) : null}
          {bundleBusy && uploadProgress === null ? (
            <p className="mt-2 text-[12.5px] text-muted-foreground">
              {t("sidebar.sessionImport.sharedBusy", { defaultValue: "Rebuilding the project..." })}
            </p>
          ) : null}
          {bundleNote ? <p className="mt-2 text-[12.5px] text-foreground/80">{bundleNote}</p> : null}
          {bundleError ? (
            <p className="mt-2 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-[12.5px] text-destructive">
              {bundleError}
            </p>
          ) : null}
        </div>


        <div className="flex justify-end gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            {t("common.close", { defaultValue: "Close" })}
          </Button>
          <Button
            size="sm"
            onClick={runImport}
            disabled={busy || selectedCount === 0}
          >
            <Download className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {t("sidebar.sessionImport.importAll", {
              count: selectedTotal,
              defaultValue: `Import ${selectedTotal} session${selectedTotal === 1 ? "" : "s"}`,
            })}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
    {/* Beside the import dialog, not inside its content: nested there, the
        folder list did not scroll. */}
    <FolderBrowserDialog
      open={browserOpen}
      startPath={null}
      onOpenChange={setBrowserOpen}
      showHidden
      onPick={(path) => {
        setBrowserOpen(false);
        void addRoot(path);
      }}
    />
    </>
  );
}
