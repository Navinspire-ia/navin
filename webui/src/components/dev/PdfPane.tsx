import { useState } from "react";
import { Download, FileWarning, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  MAX_INLINE_PDF_BYTES,
  browserRendersPdfInIframe,
  useWorkspaceFileObjectUrl,
} from "@/hooks/useWorkspaceFileObjectUrl";
import { downloadWorkspaceFile } from "@/lib/api";
import { fileNameFromPath } from "@/lib/save-blob";

interface PdfPaneProps {
  token: string;
  sessionKey: string;
  path: string;
  title?: string;
  size?: number;
  root?: string | null;
  /** Word / PowerPoint / Excel: the gateway renders the file to PDF first. */
  office?: boolean;
  /** Office files: whether the gateway has LibreOffice to render them. */
  renderAvailable?: boolean;
  /** Office files: how to install the renderer when it is missing. */
  renderHint?: string;
}

/**
 * A PDF rendered inside the editor pane.
 *
 * Its own component because it needs a hook, and the pane it lives in is
 * rendered from a helper function rather than a component of its own.
 */
export function PdfPane({
  token,
  sessionKey,
  path,
  title,
  size,
  root,
  office = false,
  renderAvailable = true,
  renderHint,
}: PdfPaneProps) {
  const { t } = useTranslation();
  const [downloading, setDownloading] = useState(false);
  const renderable = office
    ? renderAvailable && browserRendersPdfInIframe()
    : (size ?? 0) <= MAX_INLINE_PDF_BYTES && browserRendersPdfInIframe();
  const { url, loading, error } = useWorkspaceFileObjectUrl(
    token,
    sessionKey,
    path,
    renderable,
    root,
    office ? "file-render" : "file-download",
  );

  const handleDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      await downloadWorkspaceFile(
        token,
        sessionKey,
        path,
        fileNameFromPath(title || path, office ? "document" : "document.pdf"),
        "",
        root,
      );
    } finally {
      setDownloading(false);
    }
  };

  const downloadButton = (
    <button
      type="button"
      onClick={() => void handleDownload()}
      disabled={downloading}
      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-muted/60 disabled:opacity-40"
      data-testid="dev-pdf-download"
    >
      {downloading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      ) : (
        <Download className="h-3.5 w-3.5" aria-hidden />
      )}
      {t("dev.download", { defaultValue: "Download" })}
    </button>
  );

  if (url) {
    return (
      <div className="relative flex min-h-0 flex-1 flex-col">
        <iframe
          title={title || path}
          src={url}
          className="min-h-0 flex-1 border-0 bg-muted/20"
          data-testid="dev-pdf-preview"
        />
        <div className="pointer-events-none absolute right-3 top-3">
          <div className="pointer-events-auto">{downloadButton}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
      {loading ? (
        <Loader2 className="h-6 w-6 animate-spin opacity-60" aria-hidden />
      ) : (
        <FileWarning className="h-8 w-8 opacity-40" aria-hidden />
      )}
      <p className="text-[13px]">
        {loading
          ? office
            ? t("filePreview.officeRendering", {
                defaultValue: "Rendering the document with LibreOffice...",
              })
            : t("dev.pdfLoading", { defaultValue: "Loading the PDF..." })
          : error
            ? error
            : office && !renderAvailable
              ? t("filePreview.officeNoRenderer", {
                  defaultValue:
                    "Previewing Word, PowerPoint and Excel files needs LibreOffice on the machine running Navin.",
                })
              : office
                ? t("filePreview.pdfNoViewer", {
                    defaultValue:
                      "This window cannot display PDFs. Download the file to open it in your PDF reader.",
                  })
                : t("dev.pdfTooLarge", {
                    defaultValue: "This PDF is too large to display here. Download it to open it.",
                  })}
      </p>
      {!loading && office && !renderAvailable && renderHint ? (
        <p className="max-w-md break-words font-mono text-[11px] opacity-80">{renderHint}</p>
      ) : null}
      {!loading ? downloadButton : null}
    </div>
  );
}
