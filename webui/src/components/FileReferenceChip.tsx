import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import { AlertCircle, Download, Eye, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useFileWorkspaceActions } from "@/components/FileWorkspaceActionsContext";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { downloadWorkspaceFile } from "@/lib/api";
import { cn } from "@/lib/utils";

export type FileReferenceKind =
  | "default"
  | "css"
  | "html"
  | "javascript"
  | "json"
  | "markdown"
  | "notebook"
  | "python"
  | "react"
  | "typescript"
  | "csv"
  | "data";

interface FileReferenceChipProps {
  path: string;
  tooltipPath?: string;
  display?: "name" | "path";
  active?: boolean;
  className?: string;
  textClassName?: string;
  previewPath?: string;
  onOpen?: (path: string) => void;
  /** Show inline preview/download controls in chat (default: true when possible). */
  showActions?: boolean;
  testId?: string;
}

export function FileReferenceChip({
  path,
  tooltipPath,
  display = "name",
  active = false,
  className,
  textClassName,
  previewPath,
  onOpen,
  showActions = true,
  testId = "inline-file-path",
}: FileReferenceChipProps) {
  const { t } = useTranslation();
  const workspace = useFileWorkspaceActions();
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const downloadErrorTimerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (downloadErrorTimerRef.current !== null) {
        window.clearTimeout(downloadErrorTimerRef.current);
      }
    },
    [],
  );
  const { directory, name } = splitFilePath(path);
  const kind = fileKindForPath(path);
  const displayText = display === "path" ? path.replace(/\\/g, "/") : name;
  const fullPath = tooltipPath || path;
  const targetPath = previewPath || tooltipPath || path;
  const openHandler = onOpen ?? workspace?.onOpenPreview;
  const interactive = Boolean(openHandler);
  const canDownload = Boolean(showActions && workspace?.sessionKey && workspace?.token);
  const canPreview = Boolean(showActions && openHandler);
  const showActionBar = canPreview || canDownload;

  // WebKit (desktop IDE) often spends the first click dismissing the path
  // tooltip, so the eye looked dead until a second click. Pointer-down opens
  // immediately; a short latch drops the leftover click so we do not fire twice.
  const lastOpenAtRef = useRef(0);
  const openPreview = (event: MouseEvent | KeyboardEvent | PointerEvent) => {
    if (!openHandler) return;
    event.preventDefault();
    event.stopPropagation();
    const now = typeof performance !== "undefined" ? performance.now() : Date.now();
    if (now - lastOpenAtRef.current < 400) return;
    lastOpenAtRef.current = now;
    openHandler(targetPath);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    openPreview(event);
  };
  const handleDownload = async (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (!workspace || downloading) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      // Same file the eye opens: a bare `schema.sql` chip downloads the file
      // the agent wrote, wherever it wrote it.
      const resolved = workspace.resolvePath ? workspace.resolvePath(targetPath) : targetPath;
      await downloadWorkspaceFile(
        workspace.token,
        workspace.sessionKey,
        resolved,
        name || "download",
        "",
        workspace.projectPath,
      );
    } catch (err) {
      // A silent failure looks like a dead button; flag it on the icon.
      setDownloadError(err instanceof Error ? err.message : String(err));
      if (downloadErrorTimerRef.current !== null) {
        window.clearTimeout(downloadErrorTimerRef.current);
      }
      downloadErrorTimerRef.current = window.setTimeout(() => {
        downloadErrorTimerRef.current = null;
        setDownloadError(null);
      }, 6000);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <TooltipProvider delayDuration={800} skipDelayDuration={100} disableHoverableContent>
      <span
        className={cn(
          "not-prose inline-flex max-w-full items-center gap-0.5 align-baseline leading-[inherit]",
          className,
        )}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              data-testid={testId}
              aria-label={fullPath}
              role={interactive ? "button" : undefined}
              tabIndex={interactive ? 0 : undefined}
              onPointerDown={interactive ? openPreview : undefined}
              onClick={interactive ? openPreview : undefined}
              onKeyDown={interactive ? onKeyDown : undefined}
              className={cn(
                "inline-flex max-w-full items-baseline gap-[0.28em] font-medium leading-[inherit]",
                "text-sky-600 transition-colors hover:text-sky-700",
                "dark:text-sky-300 dark:hover:text-sky-200",
                interactive && [
                  "cursor-pointer rounded-[5px]",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/45",
                ],
              )}
            >
              <FileReferenceIcon kind={kind} />
              <span
                data-sheen-text={active ? displayText : undefined}
                className={cn(
                  "min-w-0 max-w-full [overflow-wrap:anywhere] sm:truncate",
                  active && "streaming-text-sheen file-reference-sheen",
                  textClassName,
                )}
              >
                {display === "path" && directory ? (
                  <>
                    <span className="text-muted-foreground/65">{directory}</span>
                    <span className="font-semibold text-sky-700 dark:text-sky-200">{name}</span>
                  </>
                ) : (
                  displayText
                )}
              </span>
            </span>
          </TooltipTrigger>
          <TooltipContent
            side="top"
            align="center"
            sideOffset={8}
            collisionPadding={12}
            className={cn(
              "max-w-[min(38rem,calc(100vw-2rem))] rounded-[10px]",
              "border-border/60 bg-popover/95 px-2.5 py-1.5",
              "break-all font-mono text-[11px] leading-snug text-popover-foreground",
              "shadow-lg backdrop-blur",
            )}
          >
            {fullPath}
          </TooltipContent>
        </Tooltip>
        {showActionBar ? (
          <span
            className="ml-0.5 inline-flex shrink-0 items-center gap-0.5 rounded-md border border-border/55 bg-muted/40 p-0.5 align-middle"
            data-testid="inline-file-actions"
          >
            {canPreview ? (
              <button
                type="button"
                className={cn(
                  "inline-flex h-6 w-6 items-center justify-center rounded",
                  "text-muted-foreground transition-colors hover:bg-background hover:text-foreground",
                  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                )}
                title={t("filePreview.open", { defaultValue: "Preview" })}
                aria-label={t("filePreview.open", { defaultValue: "Preview" })}
                data-testid="inline-file-preview"
                onPointerDown={openPreview}
                onClick={openPreview}
              >
                <Eye className="h-3.5 w-3.5" aria-hidden />
              </button>
            ) : null}
            {canDownload ? (
              <button
                type="button"
                className={cn(
                  "inline-flex h-6 w-6 items-center justify-center rounded",
                  "text-muted-foreground transition-colors hover:bg-background hover:text-foreground",
                  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                  "disabled:opacity-40",
                  downloadError && "text-destructive hover:text-destructive",
                )}
                title={
                  downloadError
                    ? t("filePreview.downloadFailed", {
                        detail: downloadError,
                        defaultValue: "Download failed: {{detail}}",
                      })
                    : t("filePreview.download", { defaultValue: "Download file" })
                }
                aria-label={t("filePreview.download", { defaultValue: "Download file" })}
                data-testid="inline-file-download"
                disabled={downloading}
                onClick={(event) => void handleDownload(event)}
              >
                {downloading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : downloadError ? (
                  <AlertCircle className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <Download className="h-3.5 w-3.5" aria-hidden />
                )}
              </button>
            ) : null}
          </span>
        ) : null}
      </span>
    </TooltipProvider>
  );
}

const BARE_FILE_EXTENSIONS =
  /\.(csv|tsv|json|jsonl|xml|xlsx|xls|xlsm|md|mdx|html|htm|txt|pdf|zip|rar|7z|gz|tar|py|pyi|ts|tsx|js|jsx|mjs|cjs|yaml|yml|toml|png|jpe?g|gif|webp|svg|mp4|webm|mp3|wav|m4a|docx?|pptx?|odt|ods)$/i;

export function isLikelyFilePath(value: string): boolean {
  const raw = value.trim();
  if (!raw || raw.includes("\n") || /\s/.test(raw)) return false;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) return false;
  if (isFilePatternReference(raw)) return false;
  if (!/[\\/]/.test(raw)) {
    if (/^(dockerfile|makefile|readme|package-lock\.json)$/i.test(raw)) return true;
    // Bare export/source names in chat: books.csv, scrape_books.py
    return BARE_FILE_EXTENSIONS.test(raw);
  }
  const normalized = raw.replace(/\\/g, "/");
  const name = normalized.split("/").filter(Boolean).pop() ?? normalized;
  if (!name || name === "." || name === "..") return false;
  if (/^(dockerfile|makefile|readme|package-lock\.json)$/i.test(name)) return true;
  return /\.[a-z0-9][a-z0-9_-]{0,12}$/i.test(name);
}

export function isFilePatternReference(value: string): boolean {
  return /[*?[\]{}]/.test(value.trim());
}

export function splitFilePath(path: string): { directory: string; name: string } {
  const normalized = path.replace(/\\/g, "/");
  const slash = normalized.lastIndexOf("/");
  if (slash < 0) return { directory: "", name: path };
  return {
    directory: normalized.slice(0, slash + 1),
    name: normalized.slice(slash + 1) || normalized,
  };
}

export function fileKindForPath(path: string): FileReferenceKind {
  const normalized = path.toLowerCase();
  const name = normalized.split(/[\\/]/).pop() ?? normalized;
  const ext = name.includes(".") ? name.split(".").pop() ?? "" : "";
  if (name === "dockerfile") {
    return "default";
  }
  switch (ext) {
    case "py":
    case "pyi":
      return "python";
    case "jsx":
    case "tsx":
      return "react";
    case "js":
    case "mjs":
    case "cjs":
      return "javascript";
    case "ts":
    case "mts":
    case "cts":
      return "typescript";
    case "html":
    case "htm":
      return "html";
    case "css":
    case "scss":
    case "sass":
      return "css";
    case "json":
    case "jsonl":
      return "json";
    case "md":
    case "mdx":
      return "markdown";
    case "ipynb":
      return "notebook";
    case "csv":
    case "tsv":
      return "csv";
    case "xlsx":
    case "xls":
    case "xml":
    case "zip":
    case "pdf":
      return "data";
    default:
      return "default";
  }
}

export function FileReferenceIcon({ kind }: { kind: FileReferenceKind }) {
  if (kind === "python") {
    return (
      <svg
        aria-hidden
        className="h-[1em] w-[1em] shrink-0 translate-y-[0.12em]"
        viewBox="0 0 24 24"
      >
        <path
          d="M11.9 2.3c-3 0-4.5.8-4.5 2.3v2.1h4.8v.8H5.5C4 7.5 3 8.8 3 10.8v2.1c0 1.8 1.1 3 2.7 3h1.6v-2.3c0-1.7 1.4-3.1 3.1-3.1h4.2c1.3 0 2.3-1 2.3-2.3V4.6c0-1.4-1.5-2.3-4.6-2.3h-.4Z"
          fill="#3776AB"
        />
        <path
          d="M12.1 21.7c3 0 4.5-.8 4.5-2.3v-2.1h-4.8v-.8h6.7c1.5 0 2.5-1.3 2.5-3.3v-2.1c0-1.8-1.1-3-2.7-3h-1.6v2.3c0 1.7-1.4 3.1-3.1 3.1H9.4c-1.3 0-2.3 1-2.3 2.3v3.6c0 1.4 1.5 2.3 4.6 2.3h.4Z"
          fill="#FFD43B"
        />
        <circle cx="9" cy="5.1" r="0.8" fill="#fff" />
        <circle cx="15" cy="18.9" r="0.8" fill="#5C3B00" opacity="0.85" />
      </svg>
    );
  }
  if (kind === "react") {
    return (
      <svg
        aria-hidden
        className="h-[0.92em] w-[0.92em] shrink-0 translate-y-[0.11em] text-sky-500 dark:text-sky-300"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="12" cy="12" r="1.9" fill="currentColor" stroke="none" />
        <ellipse cx="12" cy="12" rx="9" ry="3.7" />
        <ellipse cx="12" cy="12" rx="9" ry="3.7" transform="rotate(60 12 12)" />
        <ellipse cx="12" cy="12" rx="9" ry="3.7" transform="rotate(120 12 12)" />
      </svg>
    );
  }
  if (kind === "csv" || kind === "data") {
    return (
      <svg
        aria-hidden
        className="h-[0.92em] w-[0.92em] shrink-0 translate-y-[0.11em] text-sky-500 dark:text-sky-300"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z" />
        <path d="M14 2v5h5" />
        <path d="M8 13h8M8 17h5" />
      </svg>
    );
  }
  if (kind === "default") {
    return (
      <svg
        aria-hidden
        className="h-[0.92em] w-[0.92em] shrink-0 translate-y-[0.11em] text-sky-500 dark:text-sky-300"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z" />
        <path d="M14 2v5h5" />
      </svg>
    );
  }
  const label = fileKindLabel(kind);
  return (
    <svg
      aria-hidden
      className="h-[0.96em] w-[0.96em] shrink-0 translate-y-[0.12em] text-sky-500 dark:text-sky-300"
      viewBox="0 0 24 24"
      fill="none"
    >
      <path
        d="M7 3.5h6.6L18 7.9V19a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19V5a1.5 1.5 0 0 1 1.5-1.5Z"
        fill="currentColor"
        opacity="0.12"
      />
      <path
        d="M13.5 3.75V8h4.25M7 3.5h6.6L18 7.9V19a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19V5a1.5 1.5 0 0 1 1.5-1.5Z"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <text
        x="12"
        y="15.7"
        textAnchor="middle"
        fill="currentColor"
        fontSize={label.length > 1 ? "5.8" : "7.2"}
        fontWeight="800"
        letterSpacing="-0.2"
      >
        {label}
      </text>
    </svg>
  );
}

function fileKindLabel(kind: FileReferenceKind): string {
  switch (kind) {
    case "css":
      return "#";
    case "html":
      return "H";
    case "javascript":
      return "JS";
    case "json":
      return "{}";
    case "markdown":
      return "M";
    case "notebook":
      return "N";
    case "python":
      return "PY";
    case "typescript":
      return "TS";
    case "csv":
      return "CSV";
    default:
      return "";
  }
}
