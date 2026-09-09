// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";

import { fetchWorkspaceFileBlob } from "@/lib/api";

/**
 * A PDF this size would be loaded whole into memory before anything appears.
 * Past the cap the caller falls back to the download card, which is honest
 * about what it is doing instead of freezing on a blank panel.
 */
export const MAX_INLINE_PDF_BYTES = 40 * 1024 * 1024;

/**
 * Whether an iframe here will actually display a PDF.
 *
 * WebKitGTK, the webview the Linux desktop build runs on, ships no PDF viewer
 * and renders a blank frame instead of failing. WKWebView on macOS and
 * WebView2 on Windows both have one, so only the Linux case is refused - and
 * it gets the download card, which at least says what to do next.
 */
export function browserRendersPdfInIframe(): boolean {
  if (typeof navigator === "undefined") return true;
  const ua = navigator.userAgent;
  const isWebKitGtk =
    /\bAppleWebKit\b/i.test(ua) && !/\bChrom(?:e|ium)\b/i.test(ua) && /\bLinux\b/i.test(ua);
  return !isWebKitGtk;
}

export interface WorkspaceFileObjectUrl {
  url: string | null;
  loading: boolean;
  error: string | null;
}

/**
 * Expose a workspace file as an object URL a viewer can point at.
 *
 * The URL is revoked when the file changes or the component unmounts. Without
 * that, every preview would leak its blob for the lifetime of the tab.
 */
export function useWorkspaceFileObjectUrl(
  token: string | null | undefined,
  sessionKey: string | null | undefined,
  path: string | null | undefined,
  enabled: boolean,
  root?: string | null,
  /** "file-render" asks the gateway for the PDF rendering of an Office file. */
  route: "file-download" | "file-render" = "file-download",
): WorkspaceFileObjectUrl {
  const [state, setState] = useState<WorkspaceFileObjectUrl>({
    url: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (!enabled || !token || !sessionKey || !path) {
      setState({ url: null, loading: false, error: null });
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;
    setState({ url: null, loading: true, error: null });
    fetchWorkspaceFileBlob(token, sessionKey, path, "", root, route)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ url: objectUrl, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          url: null,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [enabled, path, root, route, sessionKey, token]);

  return state;
}
