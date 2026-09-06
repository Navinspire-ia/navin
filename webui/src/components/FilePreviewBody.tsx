import { useMemo, useState } from "react";
import { Download, FileWarning } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CodeBlock } from "@/components/CodeBlock";
import { DevHtmlPreview } from "@/components/dev/DevHtmlPreview";
import { MarkdownText } from "@/components/MarkdownText";
import { SpreadsheetPreview } from "@/components/SpreadsheetPreview";
import { browserRendersPdfInIframe } from "@/hooks/useWorkspaceFileObjectUrl";
import type { FilePreviewPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

interface FilePreviewBodyProps {
  payload: FilePreviewPayload;
  mediaUrl?: string;
  /** Office files: the PDF rendering is being produced by the gateway. */
  renderLoading?: boolean;
  /** Office files: the gateway could not render the file (message). */
  renderError?: string | null;
  onDownload?: () => void;
  /** When set, HTML preview loads the full file from disk (not the 384 KiB truncate). */
  token?: string;
  sessionKey?: string;
  projectRoot?: string | null;
}

/**
 * Delimiter of a CSV text: the one that splits the first line into the most
 * fields. French exports use ";" (the comma is the decimal separator) and
 * many tools write tab-separated files with a .csv name.
 */
export function detectCsvDelimiter(text: string): string {
  const firstLine = text.split(/\r?\n/).find((line) => line.trim() !== "") ?? "";
  let best = ",";
  let bestCount = -1;
  for (const candidate of [",", ";", "\t", "|"]) {
    let count = 0;
    let inQuotes = false;
    for (const ch of firstLine) {
      if (ch === '"') inQuotes = !inQuotes;
      else if (!inQuotes && ch === candidate) count += 1;
    }
    if (count > bestCount) {
      best = candidate;
      bestCount = count;
    }
  }
  return best;
}

export function parseCsv(text: string, maxRows = 200): string[][] {
  const delimiter = detectCsvDelimiter(text);
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (inQuotes) {
      if (ch === '"' && next === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        cell += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
      if (rows.length >= maxRows) break;
    } else if (ch !== "\r") {
      cell += ch;
    }
  }
  if (rows.length < maxRows && (cell.length > 0 || row.length > 0)) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function FilePreviewBody({
  payload,
  mediaUrl,
  renderLoading = false,
  renderError = null,
  onDownload,
  token,
  sessionKey,
  projectRoot,
}: FilePreviewBodyProps) {
  const { t } = useTranslation();
  const [showSource, setShowSource] = useState(false);
  const kind = (() => {
    const raw = payload.kind ?? "text";
    // Older gateways return kind=text with language=csv/html/markdown.
    if (raw === "text" && payload.language === "csv") return "csv";
    if (raw === "text" && (payload.language === "html" || payload.language === "htm")) {
      return "html";
    }
    if (
      raw === "text"
      && (payload.language === "markdown"
        || /\.(md|mdx|markdown)$/i.test(payload.name || payload.display_path || ""))
    ) {
      return "markdown";
    }
    return raw;
  })();
  const csvRows = useMemo(
    () => (kind === "csv" ? parseCsv(payload.content) : []),
    [kind, payload.content],
  );

  if (kind === "image" && payload.data_url) {
    return (
      <div className="relative flex min-h-full items-center justify-center bg-muted/20 p-4">
        <img
          src={payload.data_url}
          alt={payload.name || payload.display_path}
          className="max-h-[calc(100vh-8rem)] max-w-full object-contain"
          data-testid="file-preview-image"
        />
        {onDownload ? (
          <button
            type="button"
            onClick={onDownload}
            className="absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-background/90 px-2.5 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
            data-testid="file-preview-image-download"
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            {t("filePreview.download", { defaultValue: "Download file" })}
          </button>
        ) : null}
      </div>
    );
  }

  if (kind === "audio" && mediaUrl) {
    return (
      <div className="flex min-h-full flex-col items-center justify-center gap-4 p-8">
        <audio
          controls
          src={mediaUrl}
          className="w-full max-w-md"
          data-testid="file-preview-audio"
        >
          <track kind="captions" />
        </audio>
        <p className="text-xs text-muted-foreground">{formatBytes(payload.size)}</p>
        {onDownload ? (
          <button
            type="button"
            onClick={onDownload}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
            data-testid="file-preview-audio-download"
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            {t("filePreview.download", { defaultValue: "Download file" })}
          </button>
        ) : null}
      </div>
    );
  }

  if (kind === "video" && mediaUrl) {
    return (
      <div className="flex min-h-full flex-col items-center justify-center gap-3 bg-black/90 p-4">
        <video
          controls
          src={mediaUrl}
          className="max-h-[calc(100vh-10rem)] max-w-full"
          data-testid="file-preview-video"
        >
          <track kind="captions" />
        </video>
        <p className="text-xs text-muted-foreground">{formatBytes(payload.size)}</p>
        {onDownload ? (
          <button
            type="button"
            onClick={onDownload}
            className="inline-flex items-center gap-1.5 rounded-md border border-white/20 bg-black/40 px-3 py-1.5 text-xs font-medium text-white hover:bg-black/60"
            data-testid="file-preview-video-download"
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            {t("filePreview.download", { defaultValue: "Download file" })}
          </button>
        ) : null}
      </div>
    );
  }

  // Excel workbooks: the gateway read the cells, show them as a table now.
  if (kind === "spreadsheet" && payload.sheets) {
    return (
      <div className="flex h-full min-h-[40vh] w-full flex-col">
        <SpreadsheetPreview
          sheets={payload.sheets}
          density="comfortable"
          testId="file-preview-spreadsheet"
        />
      </div>
    );
  }

  // An Office file without its PDF rendering yet: say what is happening
  // (rendering, renderer missing, render failed) instead of a blank frame.
  if (kind === "office" && !mediaUrl) {
    const noViewer = !browserRendersPdfInIframe();
    const hint = renderError || (payload.render_available ? "" : payload.render_hint || "");
    const rendering = renderLoading && !renderError && payload.render_available !== false;
    return (
      <div
        className="flex h-full min-h-[40vh] w-full flex-col items-center justify-center gap-3 px-6 text-center"
        data-testid="file-preview-office"
      >
        {rendering ? (
          <>
            <span
              className="h-6 w-6 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-foreground"
              aria-hidden
            />
            <p className="text-[13px] text-muted-foreground">
              {t("filePreview.officeRendering", {
                defaultValue: "Rendering the document with LibreOffice...",
              })}
            </p>
          </>
        ) : (
          <>
            <FileWarning className="h-8 w-8 text-muted-foreground/70" aria-hidden />
            <p className="text-sm text-foreground">{payload.name || payload.display_path}</p>
            <p className="max-w-md text-xs text-muted-foreground">
              {renderError
                ? t("filePreview.officeRenderFailed", {
                    defaultValue: "The document could not be rendered for preview.",
                  })
                : payload.render_available && noViewer
                  ? t("filePreview.pdfNoViewer", {
                      defaultValue:
                        "This window cannot display PDFs. Download the file to open it in your PDF reader.",
                    })
                  : t("filePreview.officeNoRenderer", {
                      defaultValue:
                        "Previewing Word, PowerPoint and Excel files needs LibreOffice on the machine running Navin.",
                    })}
            </p>
            {hint ? (
              <p className="max-w-md break-words font-mono text-[11px] text-muted-foreground/80">
                {hint}
              </p>
            ) : null}
          </>
        )}
        <p className="text-xs text-muted-foreground">
          {[payload.mime, formatBytes(payload.size)].filter(Boolean).join(" · ")}
        </p>
        {onDownload ? (
          <button
            type="button"
            onClick={onDownload}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
            data-testid="file-preview-office-download"
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            {t("filePreview.download", { defaultValue: "Download file" })}
          </button>
        ) : null}
      </div>
    );
  }

  if ((kind === "pdf" || kind === "office") && mediaUrl) {
    // WebKitGTK (the Linux desktop webview) has no PDF viewer and paints a
    // blank frame rather than failing, so it gets the download card instead.
    if (!browserRendersPdfInIframe()) {
      return (
        <div className="flex h-full min-h-[40vh] w-full flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
          <p className="text-[13px]">
            {t("filePreview.pdfNoViewer", {
              defaultValue:
                "This window cannot display PDFs. Download the file to open it in your PDF reader.",
            })}
          </p>
          {onDownload ? (
            <button
              type="button"
              onClick={onDownload}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
              data-testid="file-preview-pdf-download"
            >
              <Download className="h-3.5 w-3.5" aria-hidden />
              {t("filePreview.download", { defaultValue: "Download file" })}
            </button>
          ) : null}
        </div>
      );
    }
    return (
      <div className="relative h-full min-h-[70vh] w-full">
        <iframe
          src={mediaUrl}
          title={payload.name || payload.display_path}
          className="h-full min-h-[70vh] w-full border-0 bg-muted/20"
          data-testid="file-preview-pdf"
        />
        {onDownload ? (
          <button
            type="button"
            onClick={onDownload}
            className="absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-background/90 px-2.5 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
            data-testid="file-preview-pdf-download"
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            {t("filePreview.download", { defaultValue: "Download file" })}
          </button>
        ) : null}
      </div>
    );
  }

  if (kind === "csv" && csvRows.length > 0 && !showSource) {
    const header = csvRows[0] ?? [];
    const body = csvRows.slice(1);
    return (
      <div className="flex min-h-full flex-col">
        <div className="flex items-center justify-between gap-2 border-b border-border/50 px-3 py-2">
          <p className="text-xs text-muted-foreground">
            {t("filePreview.csvRows", {
              defaultValue: "{{count}} rows shown",
              count: body.length,
            })}
          </p>
          <button
            type="button"
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            onClick={() => setShowSource(true)}
          >
            {t("filePreview.showSource", { defaultValue: "Show source" })}
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <table
            className="w-max min-w-full border-collapse text-left text-xs"
            data-testid="file-preview-csv"
          >
            <thead className="sticky top-0 bg-muted/90 backdrop-blur">
              <tr>
                {header.map((cell, index) => (
                  <th
                    key={`h-${index}`}
                    className="border-b border-border/60 px-3 py-2 font-medium text-foreground"
                  >
                    {cell || "\u00a0"}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, rowIndex) => (
                <tr key={`r-${rowIndex}`} className="odd:bg-muted/20">
                  {header.map((_, colIndex) => (
                    <td
                      key={`c-${rowIndex}-${colIndex}`}
                      className="max-w-[18rem] truncate border-b border-border/40 px-3 py-1.5 text-muted-foreground"
                      title={row[colIndex] ?? ""}
                    >
                      {row[colIndex] || "\u00a0"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (kind === "markdown" && !showSource) {
    return (
      <div className="flex min-h-full flex-col">
        <div className="flex items-center justify-end border-b border-border/50 px-3 py-2">
          <button
            type="button"
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            onClick={() => setShowSource(true)}
          >
            {t("filePreview.showSource", { defaultValue: "Show source" })}
          </button>
        </div>
        <div
          className="min-h-0 flex-1 overflow-auto px-5 py-4"
          data-testid="file-preview-markdown"
        >
          <MarkdownText className="text-[13.5px] leading-relaxed">
            {payload.content}
          </MarkdownText>
        </div>
      </div>
    );
  }

  if (kind === "html" && !showSource) {
    // Absolute-fill iframe: flex-1 alone collapses to ~0px / 150px default
    // height inside nested overflow containers, which looks like a blank panel.
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-end border-b border-border/50 px-3 py-2">
          <button
            type="button"
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            onClick={() => setShowSource(true)}
          >
            {t("filePreview.showSource", { defaultValue: "Show source" })}
          </button>
        </div>
        <DevHtmlPreview
          title={payload.name || "HTML preview"}
          html={payload.content}
          path={payload.path}
          token={token}
          sessionKey={sessionKey}
          root={projectRoot}
          live={false}
          revision={`${payload.size ?? 0}:${payload.path ?? ""}`}
          testId="file-preview-html"
        />
      </div>
    );
  }

  // A PDF with no URL yet is either still loading or over the inline cap.
  // Either way the download card is the truthful thing to show, rather than
  // falling through to the text renderer and drawing an empty page.
  if (kind === "binary" || kind === "pdf") {
    return (
      <div
        className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center"
        data-testid="file-preview-binary"
      >
        <FileWarning className="h-8 w-8 text-muted-foreground/70" aria-hidden />
        <p className="text-sm text-foreground">
          {t("filePreview.binaryTitle", {
            defaultValue: "Binary file",
          })}
        </p>
        <p className="max-w-sm text-xs text-muted-foreground">
          {t("filePreview.binaryHint", {
            defaultValue:
              "Preview is not available for this format. Download the file to open it locally.",
          })}
        </p>
        <p className="text-xs text-muted-foreground">
          {[payload.mime, formatBytes(payload.size)].filter(Boolean).join(" · ")}
        </p>
        {onDownload ? (
          <button
            type="button"
            onClick={onDownload}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
            data-testid="file-preview-body-download"
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            {t("filePreview.download", { defaultValue: "Download file" })}
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="min-h-full">
      {(kind === "csv" || kind === "html" || kind === "markdown") && showSource ? (
        <div className="flex justify-end border-b border-border/50 px-3 py-2">
          <button
            type="button"
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            onClick={() => setShowSource(false)}
          >
            {t("filePreview.showRendered", { defaultValue: "Show rendered" })}
          </button>
        </div>
      ) : null}
      <CodeBlock
        language={payload.language}
        code={payload.content}
        chrome="none"
        highlight
        showLineNumbers
        wrapLongLines={false}
        className={cn("min-h-full")}
      />
    </div>
  );
}
