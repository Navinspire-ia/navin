/**
 * In-app preview for note attachments.
 *
 * Opening the raw /api/notes/file URL in a browser tab leaks the token into
 * the address bar and simply cannot work once Navin ships as a packaged
 * desktop app (Windows/macOS/Linux): there is no "new tab" there. Everything
 * previews inside the product; downloading goes through a blob so the URL
 * never leaves the app either.
 */

import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Download, FileText, Loader2, Paperclip, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { MarkdownText } from "@/components/MarkdownText";
import { browserRendersPdfInIframe } from "@/hooks/useWorkspaceFileObjectUrl";
import { noteAttachmentUrl } from "@/lib/notes-api";
import { saveBlob } from "@/lib/save-blob";

import { attachmentKind } from "./attachment-kind";

export interface AttachmentPreviewTarget {
  /** Store-relative path, e.g. "_files/report.pdf". */
  path: string;
  name: string;
  size?: number;
}

/** Text preview stops here: a preview is a glance, not an editor. */
const MAX_TEXT_CHARS = 200_000;

function formatBytes(size?: number): string {
  if (!size && size !== 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function AttachmentPreview({
  token,
  target,
  onClose,
}: {
  token: string;
  target: AttachmentPreviewTarget | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState<string | null>(null);
  const [textError, setTextError] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const kind = target ? attachmentKind(target.name || target.path) : "other";
  const src = target ? noteAttachmentUrl(token, target.path) : "";

  useEffect(() => {
    setText(null);
    setTextError(false);
    if (!target || (kind !== "text" && kind !== "markdown")) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(src, { credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = await res.text();
        if (!cancelled) {
          setText(
            body.length > MAX_TEXT_CHARS
              ? `${body.slice(0, MAX_TEXT_CHARS)}\n…`
              : body,
          );
        }
      } catch {
        if (!cancelled) setTextError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [kind, src, target]);

  useEffect(() => {
    if (!target) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, target]);

  const download = useCallback(async () => {
    if (!target) return;
    setDownloading(true);
    try {
      const res = await fetch(src, { credentials: "same-origin" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      saveBlob(await res.blob(), target.name || "file");
    } catch {
      // The preview stays open; the user can retry.
    } finally {
      setDownloading(false);
    }
  }, [src, target]);

  return (
    <AnimatePresence>
      {target ? (
        <motion.div
          key="attachment-preview"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
          role="dialog"
          aria-modal="true"
          aria-label={target.name}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: 8 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border/70 bg-background shadow-2xl"
          >
            <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-4 py-2.5">
              <Paperclip className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
                {target.name}
              </span>
              {target.size !== undefined ? (
                <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                  {formatBytes(target.size)}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => void download()}
                disabled={downloading}
                className="flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:opacity-60"
              >
                {downloading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Download className="h-3.5 w-3.5" aria-hidden />
                )}
                {t("notes.preview.download", { defaultValue: "Download" })}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                aria-label={t("notes.preview.close", { defaultValue: "Close" })}
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-auto bg-muted/20">
              {kind === "image" ? (
                <div className="flex min-h-full items-center justify-center p-4">
                  <img
                    src={src}
                    alt={target.name}
                    className="max-h-[70vh] max-w-full rounded-lg object-contain"
                  />
                </div>
              ) : kind === "pdf" ? (
                browserRendersPdfInIframe() ? (
                  <iframe src={src} title={target.name} className="h-[70vh] w-full" />
                ) : (
                  // WebKitGTK has no PDF viewer: it would paint a blank frame.
                  <PreviewFallback
                    label={t("notes.preview.pdfNoViewer", {
                      defaultValue:
                        "This window cannot display PDFs. Download the file to open it.",
                    })}
                  />
                )
              ) : kind === "audio" ? (
                <div className="flex items-center justify-center p-10">
                  <audio src={src} controls className="w-full max-w-md" />
                </div>
              ) : kind === "video" ? (
                <div className="flex items-center justify-center p-4">
                  <video src={src} controls className="max-h-[70vh] max-w-full rounded-lg" />
                </div>
              ) : kind === "text" || kind === "markdown" ? (
                textError ? (
                  <PreviewFallback
                    label={t("notes.preview.loadFailed", {
                      defaultValue: "Could not load this file.",
                    })}
                  />
                ) : text === null ? (
                  <div className="flex items-center justify-center p-10 text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  </div>
                ) : kind === "markdown" ? (
                  <div className="p-5">
                    <MarkdownText>{text}</MarkdownText>
                  </div>
                ) : (
                  <pre className="whitespace-pre-wrap break-words p-4 font-mono text-[12px] leading-relaxed text-foreground/90">
                    {text}
                  </pre>
                )
              ) : (
                <PreviewFallback
                  label={t("notes.preview.noPreview", {
                    defaultValue: "No preview for this file type. Use Download.",
                  })}
                />
              )}
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function PreviewFallback({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-12 text-muted-foreground">
      <FileText className="h-8 w-8 opacity-60" aria-hidden />
      <p className="text-[12.5px]">{label}</p>
    </div>
  );
}
