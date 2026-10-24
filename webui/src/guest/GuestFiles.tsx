// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, Eye, FileText, Loader2, RefreshCw, Search } from "lucide-react";

import { cn } from "@/lib/utils";

import { GuestFileViewer, previewKind } from "./GuestFileViewer";

export interface SharedFile {
  path: string;
  size: number;
  modified: number;
}

/** What the page needs from the share (GuestApp wires it to the relay). */
export interface GuestFilesSource {
  list: () => Promise<{ files: SharedFile[]; truncated: boolean; project: string }>;
  fetch: (path: string, options?: { preview?: boolean }) => Promise<{ blob: Blob; name: string }>;
}

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * The shared project's files for a guest allowed to download: find one,
 * open it in the browser or save it. Secrets never appear (navin/share/files.py).
 */
export function GuestFiles({ source, hidden }: { source: GuestFilesSource; hidden: boolean }) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<SharedFile[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [viewing, setViewing] = useState<SharedFile | null>(null);
  const fetchPreview = useCallback((path: string) => source.fetch(path, { preview: true }), [source]);

  const load = () => {
    setLoading(true);
    setError("");
    void source
      .list()
      .then((result) => {
        setFiles(result.files);
        setTruncated(result.truncated);
      })
      .catch(() => setError(t("chat.liveShare.guest.files.unavailable", { defaultValue: "The files are not reachable right now." })))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!hidden && files === null && !loading) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- first opening only
  }, [hidden]);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = files ?? [];
    return (needle ? list.filter((file) => file.path.toLowerCase().includes(needle)) : list).slice(0, 400);
  }, [files, query]);

  const download = async (file: SharedFile) => {
    setBusy(file.path);
    setError("");
    try {
      const { blob, name } = await source.fetch(file.path);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (reason) {
      const code = reason instanceof Error ? reason.message : "";
      setError(
        code === "too_large"
          ? t("chat.liveShare.guest.files.tooLarge", { defaultValue: "This file is too large to send through the share (40 MB max)." })
          : t("chat.liveShare.guest.files.failed", { defaultValue: "This file could not be sent. Try again." }),
      );
    } finally {
      setBusy(null);
    }
  };

  const viewingKind = viewing ? previewKind(viewing.path) : null;
  if (viewing && viewingKind) {
    return (
      <div className={cn("flex min-h-0 flex-1 flex-col", hidden && "hidden")}>
        <GuestFileViewer
          path={viewing.path}
          kind={viewingKind}
          fetchPreview={fetchPreview}
          onDownload={() => void download(viewing)}
          onClose={() => setViewing(null)}
        />
        {error ? <p className="border-t border-border/60 px-3 py-1.5 text-[12px] text-destructive">{error}</p> : null}
      </div>
    );
  }

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", hidden && "hidden")}>
      <div className="flex items-center gap-2 border-b border-border/60 px-2 py-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md border border-border/70 px-2">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("chat.liveShare.guest.files.search", { defaultValue: "Find a file (report.pdf, .xlsx...)" })}
            className="h-8 min-w-0 flex-1 bg-transparent text-[13px] outline-none"
          />
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          aria-label={t("chat.liveShare.guest.files.refresh", { defaultValue: "Refresh the list" })}
          title={t("chat.liveShare.guest.files.refresh", { defaultValue: "Refresh the list" })}
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />}
        </button>
      </div>
      {error ? <p className="border-b border-border/60 px-3 py-1.5 text-[12px] text-destructive">{error}</p> : null}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {files === null ? (
          <div className="flex justify-center p-6"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden /></div>
        ) : shown.length === 0 ? (
          <p className="p-6 text-center text-[13px] text-muted-foreground">
            {t("chat.liveShare.guest.files.empty", { defaultValue: "No file here." })}
          </p>
        ) : (
          <ul className="divide-y divide-border/50">
            {shown.map((file) => (
              <li key={file.path} className="flex items-center gap-2 px-3 py-1.5">
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px]" title={file.path}>{file.path.split("/").pop()}</span>
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {file.path.includes("/") ? `${file.path.slice(0, file.path.lastIndexOf("/"))} · ` : ""}{sizeLabel(file.size)}
                  </span>
                </span>
                {busy === file.path ? (
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
                ) : (
                  <>
                    {previewKind(file.path) ? (
                      <button
                        type="button"
                        onClick={() => {
                          setError("");
                          setViewing(file);
                        }}
                        aria-label={t("chat.liveShare.guest.files.preview", { defaultValue: "Preview" })}
                        title={t("chat.liveShare.guest.files.preview", { defaultValue: "Preview" })}
                        className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
                      >
                        <Eye className="h-3.5 w-3.5" aria-hidden />
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => void download(file)}
                      aria-label={t("chat.liveShare.guest.files.download", { defaultValue: "Download" })}
                      title={t("chat.liveShare.guest.files.download", { defaultValue: "Download" })}
                      className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
                    >
                      <Download className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        {truncated ? (
          <p className="px-3 py-2 text-[11.5px] text-muted-foreground">
            {t("chat.liveShare.guest.files.truncated", { defaultValue: "Large project: only the 5,000 most recent files are listed." })}
          </p>
        ) : null}
      </div>
    </div>
  );
}
