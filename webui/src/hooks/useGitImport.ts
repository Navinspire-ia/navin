// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, cloneGitProject } from "@/lib/api";
import type { GitClonePayload } from "@/lib/types";
import {
  clearPendingGitImport,
  gitAuthRequired,
  normalizeGitImportUrl,
  pendingGitImport,
  rememberPendingGitImport,
} from "@/lib/git-auth";
import { openSettingsSection } from "@/lib/settings-events";

export type GitImportError =
  | { kind: "auth"; host: string; message: string }
  | { kind: "other"; message: string };

export interface GitImportState {
  url: string;
  setUrl: (value: string) => void;
  cloning: boolean;
  error: GitImportError | null;
  /** The user went to sign in for the host: offer Retry next to Connect. */
  connectRequested: boolean;
  /** The inline sign-in card for the host is open under the field. */
  connectOpen: boolean;
  /** Gateway token, for the inline sign-in card. */
  token: string | null | undefined;
  importRepo: () => Promise<void>;
  /** Open the inline sign-in card (toggles it closed when already open). */
  connect: () => void;
  /** Secondary path: sign in from Settings > Git & GitHub, then Retry. */
  openSettings: () => void;
  /** The inline card signed in: close it and clone again right away. */
  onConnected: () => Promise<void>;
  /** Clear the field when its menu reopens, unless a sign-in retry is pending. */
  reset: () => void;
}

/**
 * Import Git, shared by the Code empty state / project menu and the composer's
 * project row: clone, and when the repository is private say so plainly and
 * open the sign-in card right there (Settings stays a secondary link), then
 * clone again once the host is connected instead of showing a raw 401.
 */
export function useGitImport({
  token,
  parent,
  disabled,
  onImported,
}: {
  token: string | null | undefined;
  parent?: string | null;
  disabled?: boolean;
  onImported: (result: GitClonePayload) => void;
}): GitImportState {
  const { t } = useTranslation();
  const [url, setUrlState] = useState(() => pendingGitImport()?.url ?? "");
  const [error, setError] = useState<GitImportError | null>(() => {
    const pending = pendingGitImport();
    return pending ? { kind: "auth", host: pending.host, message: "" } : null;
  });
  const [connectRequested, setConnectRequested] = useState(() => pendingGitImport() !== null);
  const [connectOpen, setConnectOpen] = useState(false);
  const [cloning, setCloning] = useState(false);
  const cloningRef = useRef(false);

  const setUrl = useCallback((value: string) => {
    setUrlState(value);
    setError(null);
    setConnectRequested(false);
    setConnectOpen(false);
    clearPendingGitImport();
  }, []);

  const reset = useCallback(() => {
    if (pendingGitImport()) return;
    setUrlState("");
    setError(null);
    setConnectRequested(false);
    setConnectOpen(false);
  }, []);

  const importRepo = useCallback(async () => {
    const target = normalizeGitImportUrl(url);
    if (!target || !token || cloningRef.current || disabled) return;
    cloningRef.current = true;
    setCloning(true);
    setError(null);
    try {
      const result = await cloneGitProject(token, {
        url: target,
        parent: parent ?? undefined,
      });
      clearPendingGitImport();
      setConnectRequested(false);
      setConnectOpen(false);
      onImported(result);
    } catch (err) {
      const auth = gitAuthRequired(err);
      if (auth) {
        setError({ kind: "auth", host: auth.host, message: auth.message });
        return;
      }
      clearPendingGitImport();
      setConnectRequested(false);
      setConnectOpen(false);
      let detail =
        err instanceof Error ? err.message : String(err);
      if (
        err instanceof ApiError
        && err.status >= 500
        && (!detail || /^HTTP\s*5\d\d$/i.test(detail.trim()))
      ) {
        detail = t("dev.project.gitImportGatewayDown", {
          defaultValue: "Gateway unavailable (HTTP {{status}}). Wait a second and retry.",
          status: err.status,
        });
      }
      setError({
        kind: "other",
        message: t("dev.project.gitImportFailed", {
          defaultValue: "Import failed: {{detail}}",
          detail,
        }),
      });
    } finally {
      cloningRef.current = false;
      setCloning(false);
    }
  }, [disabled, onImported, parent, t, token, url]);

  const connect = useCallback(() => {
    if (error?.kind !== "auth") return;
    setConnectOpen((open) => !open);
  }, [error]);

  const openSettings = useCallback(() => {
    if (error?.kind !== "auth") return;
    rememberPendingGitImport(url, error.host);
    setConnectRequested(true);
    setConnectOpen(false);
    openSettingsSection("git");
  }, [error, url]);

  const onConnected = useCallback(async () => {
    setConnectOpen(false);
    await importRepo();
  }, [importRepo]);

  return {
    url,
    setUrl,
    cloning,
    error,
    connectRequested,
    connectOpen,
    token,
    importRepo,
    connect,
    openSettings,
    onConnected,
    reset,
  };
}
