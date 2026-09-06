import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { fetchWorkspaceFileBlob } from "@/lib/api";

/**
 * HTML file preview for Navin Code and chat file previews.
 *
 * Interactive pages (Three.js, onclick, import maps) need:
 * - ``allow-scripts`` (empty sandbox="" blocks every script)
 * - the **full** file (file-preview truncates at 384 KiB and cuts scripts mid-way)
 * - a real document URL (blob:), not a clipped srcDoc of the truncated buffer
 *
 * Live edits (dirty buffer) use a blob built from the editor text instead.
 */

/** Same permissions as DevPreviewBrowser for workspace HTML the user opened. */
export const HTML_PREVIEW_SANDBOX =
  "allow-scripts allow-same-origin allow-forms allow-modals allow-popups";

function htmlObjectUrl(source: Blob | string): string {
  const blob =
    typeof source === "string"
      ? new Blob([source], { type: "text/html;charset=utf-8" })
      : new Blob([source], { type: "text/html;charset=utf-8" });
  return URL.createObjectURL(blob);
}

export function DevHtmlPreview({
  title,
  html,
  path,
  token,
  sessionKey,
  root,
  /** When true, render ``html`` (editor buffer) instead of re-fetching disk. */
  live = false,
  /** Bump after Reload / agent write so a disk blob is rebuilt. */
  revision,
  className,
  testId = "dev-html-preview",
}: {
  title: string;
  html: string;
  path?: string | null;
  token?: string | null;
  sessionKey?: string | null;
  root?: string | null;
  live?: boolean;
  revision?: string | number | null;
  className?: string;
  testId?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setLoading(true);
    setError(null);

    const apply = (next: string) => {
      if (cancelled) {
        URL.revokeObjectURL(next);
        return;
      }
      objectUrl = next;
      setUrl(next);
      setLoading(false);
    };

    if (live || !token || !sessionKey || !path) {
      apply(htmlObjectUrl(html || "<!DOCTYPE html><html><body></body></html>"));
      return () => {
        cancelled = true;
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      };
    }

    fetchWorkspaceFileBlob(token, sessionKey, path, "", root)
      .then(async (blob) => {
        if (cancelled) return;
        apply(htmlObjectUrl(blob));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Disk fetch failed: still try the in-memory buffer so Preview is not blank.
        try {
          apply(htmlObjectUrl(html || "<!DOCTYPE html><html><body></body></html>"));
          setError(err instanceof Error ? err.message : String(err));
        } catch {
          setUrl(null);
          setLoading(false);
          setError(err instanceof Error ? err.message : String(err));
        }
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [html, live, path, revision, root, sessionKey, token]);

  return (
    <div
      className={[
        "relative min-h-0 flex-1 overflow-hidden",
        className ?? "",
      ].join(" ")}
      data-testid={`${testId}-frame`}
    >
      {loading && !url ? (
        <div className="absolute inset-0 flex items-center justify-center text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
        </div>
      ) : null}
      {url ? (
        <iframe
          key={url}
          title={title}
          sandbox={HTML_PREVIEW_SANDBOX}
          src={url}
          className="absolute inset-0 h-full w-full border-0 bg-[#0d1117]"
          data-testid={testId}
        />
      ) : null}
      {error && url ? (
        <p className="pointer-events-none absolute bottom-2 left-2 right-2 truncate rounded bg-background/90 px-2 py-1 text-[11px] text-muted-foreground">
          {error}
        </p>
      ) : null}
      {!loading && !url && error ? (
        <div className="absolute inset-0 flex items-center justify-center px-6 text-center text-[13px] text-destructive">
          {error}
        </div>
      ) : null}
    </div>
  );
}
