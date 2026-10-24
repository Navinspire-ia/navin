// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, FolderOpen, Loader2, PackageOpen } from "lucide-react";

import { FolderBrowserDialog } from "@/components/dev/DevProjectSelector";
import { LiveSharePanel } from "@/components/LiveSharePanel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { exportSessionBundle, type SessionBundleExportResult } from "@/lib/api";
import { nativeFolderPickerAvailable, pickNativeFolder } from "@/lib/native-dialog";
import { useClient } from "@/providers/ClientProvider";

/**
 * Share a chat: send a copy as one .navin-session file (conversation, the
 * project with its git history and pending changes, the project memory). The
 * person who receives it opens it from "Import sessions" and continues on
 * their machine. Secrets (.env, keys, certificates) never leave.
 */
export function ShareSessionDialog({
  open,
  chatId,
  title,
  siteUrl = null,
  mode = "share",
  onOpenChange,
}: {
  open: boolean;
  chatId: string | null;
  title: string;
  /** Opened from the Browser panel: the running site to share with the link. */
  siteUrl?: string | null;
  /** "export" keeps only the .navin-session copy, like the guest page download. */
  mode?: "share" | "export";
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SessionBundleExportResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [browserOpen, setBrowserOpen] = useState(false);

  useEffect(() => {
    if (!open) {
      setBusy(false);
      setError("");
      setResult(null);
      setCopied(false);
    }
  }, [open]);

  async function exportTo(dir: string | null) {
    if (!chatId) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(await exportSessionBundle(token, chatId, dir));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function chooseFolder() {
    if (nativeFolderPickerAvailable()) {
      try {
        const picked = await pickNativeFolder();
        if (picked) await exportTo(picked);
        return;
      } catch {
        // Refused or missing bridge: the in-app browser below.
      }
    }
    setBrowserOpen(true);
  }

  async function copyPath() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.path);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard denied: the path stays selectable on screen.
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
          {/* No visible header: the live share panel and the copy box carry their own titles. */}
          <DialogHeader className="sr-only">
            <DialogTitle>{t("chat.share.title", { defaultValue: "Share \"{{title}}\"", title })}</DialogTitle>
            <DialogDescription>
              {mode === "export"
                ? t("chat.share.exportDescription", {
                    defaultValue: "Download this chat as a .navin-session file, then open it from the sidebar on any Navin.",
                  })
                : t("chat.liveShare.title", { defaultValue: "Live share (link)" })}
            </DialogDescription>
          </DialogHeader>

          {mode === "share" ? <LiveSharePanel chatId={chatId} open={open} siteUrl={siteUrl} /> : null}

          <div className="rounded-lg border border-border/70 p-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <PackageOpen className="h-4 w-4 text-muted-foreground" aria-hidden />
              {t("chat.share.copyTitle", { defaultValue: "Send a copy (.navin-session file)" })}
            </div>
            <p className="mt-1 text-[12.5px] text-muted-foreground">
              {t("chat.share.secretsNote", {
                defaultValue: "Secrets never travel: .env files, private keys and certificates are left out.",
              })}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" disabled={busy || !chatId} onClick={() => void chooseFolder()}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <FolderOpen className="h-4 w-4" aria-hidden />}
                {busy
                  ? t("chat.share.exporting", { defaultValue: "Packing the session..." })
                  : t("chat.share.chooseFolder", { defaultValue: "Choose a folder and export" })}
              </Button>
              <Button size="sm" variant="ghost" disabled={busy || !chatId} onClick={() => void exportTo(null)}>
                {t("chat.share.toDownloads", { defaultValue: "Export to Downloads" })}
              </Button>
            </div>

            {error ? (
              <p className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-[12.5px] text-destructive">
                {error}
              </p>
            ) : null}

            {result ? (
              <div className="mt-3 space-y-2 text-[12.5px]">
                <div className="flex items-start gap-2 rounded-md bg-muted/50 px-2.5 py-2">
                  <code className="min-w-0 flex-1 break-all text-foreground/90">{result.path}</code>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-6 w-6 shrink-0"
                    aria-label={t("chat.share.copyPath", { defaultValue: "Copy the path" })}
                    onClick={() => void copyPath()}
                  >
                    {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                  </Button>
                </div>
                <p className="text-muted-foreground">
                  {t("chat.share.contents", {
                    defaultValue: "Project: {{mode}}, {{count}} file(s) besides the history.",
                    mode: result.project_mode === "git"
                      ? t("chat.share.modeGit", { defaultValue: "git (history + pending changes)" })
                      : result.project_mode === "files"
                        ? t("chat.share.modeFiles", { defaultValue: "files" })
                        : t("chat.share.modeNone", { defaultValue: "none" }),
                    count: result.files,
                  })}
                </p>
                {result.excluded_secrets.length ? (
                  <p className="text-muted-foreground">
                    {t("chat.share.excluded", {
                      defaultValue: "Left out, to recreate on the other machine: {{list}}",
                      list: result.excluded_secrets.slice(0, 8).join(", "),
                    })}
                  </p>
                ) : null}
                {result.skipped_large.length ? (
                  <p className="text-muted-foreground">
                    {t("chat.share.tooLarge", {
                      defaultValue: "Too large to pack: {{list}}",
                      list: result.skipped_large.slice(0, 8).join(", "),
                    })}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
      <FolderBrowserDialog
        open={browserOpen}
        startPath={null}
        onOpenChange={setBrowserOpen}
        onPick={(path) => {
          setBrowserOpen(false);
          void exportTo(path);
        }}
      />
    </>
  );
}
