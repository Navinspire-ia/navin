import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { AlertCircle, Download, FileText, Loader2, Maximize2, Minimize2, Printer, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { FilePreviewBody } from "@/components/FilePreviewBody";
import {
  MAX_INLINE_PDF_BYTES,
  browserRendersPdfInIframe,
  useWorkspaceFileObjectUrl,
} from "@/hooks/useWorkspaceFileObjectUrl";
import { splitFilePath } from "@/components/FileReferenceChip";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import {
  ApiError,
  downloadWorkspaceFile,
  fetchDevFilePreview,
  workspaceFileDownloadUrl,
} from "@/lib/api";
import { printHtmlDocument } from "@/lib/printHtml";
import type { FilePreviewPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

interface FilePreviewPanelProps {
  sessionKey: string;
  path: string;
  token: string;
  projectRoot?: string | null;
  desktopWidth?: number;
  isClosing?: boolean;
  onResizeStart?: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onClose: () => void;
}

type PreviewState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; payload: FilePreviewPayload };

export function FilePreviewPanel({
  sessionKey,
  path,
  token,
  projectRoot,
  desktopWidth = 544,
  isClosing = false,
  onResizeStart,
  onClose,
}: FilePreviewPanelProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<PreviewState>({ status: "loading" });
  const [entered, setEntered] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [previewFullscreen, setPreviewFullscreen] = useState(false);

  useEffect(() => {
    setPreviewFullscreen(false);
  }, [path]);

  useEffect(() => {
    if (!previewFullscreen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setPreviewFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [previewFullscreen]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    setDownloadError(null);
    // any=1 so images, audio, video and archives open in the panel (download /
    // dedicated viewers) instead of a 415 binary error.
    fetchDevFilePreview(token, sessionKey, path, "", projectRoot)
      .then((payload) => {
        if (!cancelled) setState({ status: "ready", payload });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof ApiError
          ? (error.status === 404 && /API route not found/i.test(error.message)
            ? t("filePreview.routeMissing", {
              defaultValue: "File preview needs the latest gateway. Restart navin gateway and try again.",
            })
            : error.message)
          : t("filePreview.failed", { defaultValue: "Could not preview this file." });
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [path, projectRoot, sessionKey, t, token]);

  const displayPath = state.status === "ready" ? state.payload.display_path : path;
  const previewPath = state.status === "ready" ? state.payload.path : displayPath;
  const normalizedPreviewPath = previewPath.replace(/\\/g, "/");
  const hasRootPrefix = normalizedPreviewPath.startsWith("/");
  const { name } = splitFilePath(displayPath);
  const fileName = (state.status === "ready" ? state.payload.name : undefined) || name || displayPath;
  const directoryParts = useMemo(() => {
    const parts = normalizedPreviewPath.split("/").filter(Boolean);
    return parts.length > 1 ? parts.slice(0, -1) : [];
  }, [normalizedPreviewPath]);
  const breadcrumbTitle = `${hasRootPrefix ? "/" : ""}${[
    ...directoryParts,
    fileName,
  ].join("/")}`;

  const mediaUrl = useMemo(() => {
    if (state.status !== "ready") return undefined;
    const kind = state.payload.kind;
    if (kind !== "audio" && kind !== "video") return undefined;
    return workspaceFileDownloadUrl(
      token,
      sessionKey,
      state.payload.path || path,
      "",
      projectRoot,
    );
  }, [path, projectRoot, sessionKey, state, token]);

  // A PDF cannot use the plain download URL: that route answers with
  // Content-Disposition attachment, which an iframe honours by downloading.
  const pdfPayload = state.status === "ready" && state.payload.kind === "pdf"
    ? state.payload
    : null;
  const pdf = useWorkspaceFileObjectUrl(
    token,
    sessionKey,
    pdfPayload ? pdfPayload.path || path : null,
    Boolean(pdfPayload)
      && (pdfPayload?.size ?? 0) <= MAX_INLINE_PDF_BYTES
      && browserRendersPdfInIframe(),
    projectRoot,
  );

  // Word / PowerPoint / Excel: the gateway renders the file to PDF through
  // LibreOffice (file-render route) and the same viewer shows it.
  const officePayload = state.status === "ready" && state.payload.kind === "office"
    ? state.payload
    : null;
  const office = useWorkspaceFileObjectUrl(
    token,
    sessionKey,
    officePayload ? officePayload.path || path : null,
    Boolean(officePayload?.render_available) && browserRendersPdfInIframe(),
    projectRoot,
    "file-render",
  );

  const isHtmlPreview =
    state.status === "ready" &&
    (state.payload.kind === "html" ||
      /\.html?$/i.test(state.payload.name || fileName || path));

  const handleDownload = async () => {
    setDownloading(true);
    setDownloadError(null);
    try {
      const targetPath = state.status === "ready"
        ? (state.payload.path || path)
        : path;
      const downloadName =
        isHtmlPreview && fileName && !/\.html?$/i.test(fileName)
          ? `${fileName}.html`
          : fileName;
      await downloadWorkspaceFile(
        token,
        sessionKey,
        targetPath,
        downloadName,
        "",
        projectRoot,
      );
    } catch (error: unknown) {
      const message = error instanceof ApiError
        ? error.message
        : t("filePreview.downloadFailed", { defaultValue: "Could not download this file." });
      setDownloadError(message);
    } finally {
      setDownloading(false);
    }
  };

  const handleExportPdf = () => {
    if (state.status !== "ready" || !state.payload.content) {
      setDownloadError(
        t("filePreview.pdfUnavailable", {
          defaultValue: "PDF export needs a loaded HTML preview.",
        }),
      );
      return;
    }
    setDownloadError(null);
    printHtmlDocument(state.payload.content);
  };

  return (
    <aside
      aria-label={t("filePreview.aria", { defaultValue: "File preview" })}
      style={{
        "--file-preview-width": `${desktopWidth}px`,
        "--file-preview-slot-width": !entered || isClosing ? "0px" : `${desktopWidth}px`,
      } as CSSProperties}
      className={cn(
        isClosing && "pointer-events-none",
        previewFullscreen
          ? "fixed inset-y-0 right-0 z-[80] w-auto left-0 overflow-visible lg:left-[var(--navin-host-sidebar,0px)]"
          : cn(
              "absolute inset-y-0 right-0 z-30 w-[min(100vw,var(--file-preview-slot-width))] overflow-hidden",
              "transition-[width] duration-300 ease-out will-change-[width]",
              "md:relative md:z-auto md:w-[var(--file-preview-slot-width)] md:min-w-0 md:shrink-0",
            ),
      )}
      data-testid="file-preview-panel"
      data-file-preview-panel
    >
      <div
        className={cn(
          "flex flex-col overflow-hidden bg-background",
          previewFullscreen
            ? "h-full w-full"
            : cn(
                "absolute inset-y-0 right-0 w-[min(100vw,var(--file-preview-width))] pb-[env(safe-area-inset-bottom)] md:w-[var(--file-preview-width)] md:pb-0",
                "border-l border-border/70 shadow-2xl md:shadow-none",
                "transition-[opacity,transform] duration-300 ease-out will-change-transform",
                !entered || isClosing ? "translate-x-full opacity-0" : "translate-x-0 opacity-100",
                "motion-reduce:translate-x-0",
              ),
        )}
      >
        {onResizeStart ? (
          <button
            type="button"
            aria-label={t("filePreview.resize", { defaultValue: "Resize file preview" })}
            className={cn(
              "group absolute inset-y-0 left-0 z-20 hidden w-3 -translate-x-1/2 cursor-col-resize touch-none md:flex",
              "items-stretch justify-center focus-visible:outline-none",
            )}
            onPointerDown={onResizeStart}
          >
            <span
              aria-hidden
              className={cn(
                "h-full w-px bg-foreground/25 opacity-0 transition-opacity",
                "group-hover:opacity-100 group-focus-visible:bg-ring group-focus-visible:opacity-100",
              )}
            />
          </button>
        ) : null}
        <div className="flex min-h-0 flex-1 flex-col">
          <div
            className={cn(
              "flex h-11 shrink-0 items-center gap-2 border-b border-border/60 pl-3",
              // Bell is fixed over the top-right corner; reserve room so Close
              // and Download do not sit underneath it.
              NOTIFICATION_GUTTER,
            )}
            title={previewPath}
          >
            {/* Doc name only; the full path stays available as a tooltip. */}
            <nav
              aria-label={t("filePreview.breadcrumb", { defaultValue: "File path" })}
              className="flex min-w-0 flex-1 items-center overflow-hidden text-sm leading-5"
              title={breadcrumbTitle}
              data-testid="file-preview-breadcrumb"
            >
              <span
                className="min-w-0 truncate rounded-[4px] px-1 py-0.5 font-medium text-foreground"
                data-testid="file-preview-title"
              >
                {fileName}
              </span>
            </nav>
            {isHtmlPreview ? (
              <button
                type="button"
                onClick={handleExportPdf}
                disabled={state.status !== "ready"}
                className={cn(
                  "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2",
                  "text-[12px] font-medium text-muted-foreground transition-colors",
                  "hover:bg-muted hover:text-foreground",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  "disabled:pointer-events-none disabled:opacity-40",
                )}
                title={t("filePreview.exportPdf", {
                  defaultValue: "Export PDF (print dialog)",
                })}
                aria-label={t("filePreview.exportPdf", {
                  defaultValue: "Export PDF (print dialog)",
                })}
                data-testid="file-preview-export-pdf"
              >
                <Printer className="h-3.5 w-3.5" aria-hidden />
                <span className="hidden sm:inline">PDF</span>
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setPreviewFullscreen((open) => !open)}
              className={cn(
                "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2",
                "text-[12px] font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                previewFullscreen
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              title={
                previewFullscreen
                  ? t("filePreview.exitFullscreen", { defaultValue: "Exit full screen" })
                  : t("filePreview.fullscreen", { defaultValue: "Full screen" })
              }
              aria-label={
                previewFullscreen
                  ? t("filePreview.exitFullscreen", { defaultValue: "Exit full screen" })
                  : t("filePreview.fullscreen", { defaultValue: "Full screen" })
              }
              aria-pressed={previewFullscreen}
              data-testid="file-preview-fullscreen"
            >
              {previewFullscreen ? (
                <Minimize2 className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <Maximize2 className="h-3.5 w-3.5" aria-hidden />
              )}
              <span className="hidden sm:inline">
                {previewFullscreen
                  ? t("filePreview.exitFullscreen", { defaultValue: "Exit" })
                  : t("filePreview.fullscreen", { defaultValue: "Full screen" })}
              </span>
            </button>
            <button
              type="button"
              onClick={() => void handleDownload()}
              disabled={downloading || state.status === "loading"}
              className={cn(
                "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2",
                "text-[12px] font-medium text-muted-foreground transition-colors",
                "hover:bg-muted hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                "disabled:pointer-events-none disabled:opacity-40",
              )}
              title={
                isHtmlPreview
                  ? t("filePreview.downloadHtml", { defaultValue: "Download HTML" })
                  : t("filePreview.download", { defaultValue: "Download file" })
              }
              aria-label={
                isHtmlPreview
                  ? t("filePreview.downloadHtml", { defaultValue: "Download HTML" })
                  : t("filePreview.download", { defaultValue: "Download file" })
              }
              data-testid="file-preview-download"
            >
              {downloading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : isHtmlPreview ? (
                <FileText className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <Download className="h-3.5 w-3.5" aria-hidden />
              )}
              <span className="hidden sm:inline">
                {isHtmlPreview
                  ? t("filePreview.downloadHtmlShort", { defaultValue: "HTML" })
                  : null}
              </span>
            </button>
            <button
              type="button"
              onClick={onClose}
              className={cn(
                "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
                "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
              title={t("filePreview.close", { defaultValue: "Close file preview" })}
              aria-label={t("filePreview.close", { defaultValue: "Close file preview" })}
              data-testid="file-preview-close"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>

          {downloadError ? (
            <div className="border-b border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {downloadError}
            </div>
          ) : null}

          <div
            className={cn(
              "min-h-0 flex-1",
              // HTML scrolls inside the iframe; other kinds use the panel scroller.
              isHtmlPreview ? "overflow-hidden" : "overflow-auto",
            )}
          >
            {state.status === "loading" ? (
              <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("filePreview.loading", { defaultValue: "Loading preview..." })}
              </div>
            ) : state.status === "error" ? (
              <div className="flex h-full flex-col items-center justify-center gap-4 px-8 text-center text-sm text-muted-foreground">
                <div className="max-w-sm">
                  <AlertCircle
                    className="mx-auto mb-3 h-5 w-5 text-muted-foreground/70"
                    aria-hidden
                  />
                  <p>{state.message}</p>
                </div>
                {!/file not found/i.test(state.message) ? (
                  <button
                    type="button"
                    onClick={() => void handleDownload()}
                    disabled={downloading}
                    className={cn(
                      "inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-xs",
                      "text-foreground hover:bg-muted",
                    )}
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden />
                    {t("filePreview.downloadAnyway", { defaultValue: "Download anyway" })}
                  </button>
                ) : null}
              </div>
            ) : (
              <div className="flex h-full min-h-0 flex-col">
                {state.payload.truncated ? (
                  <div className="mx-4 mt-3 shrink-0 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-200">
                    {t("filePreview.truncated", {
                      defaultValue: "Preview is truncated because this file is large.",
                    })}
                  </div>
                ) : null}
                <div className="min-h-0 flex-1">
                  <FilePreviewBody
                    payload={state.payload}
                    mediaUrl={pdf.url ?? office.url ?? mediaUrl}
                    renderLoading={office.loading}
                    renderError={office.error}
                    onDownload={() => void handleDownload()}
                    token={token}
                    sessionKey={sessionKey}
                    projectRoot={projectRoot}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
