// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Component, type ReactNode } from "react";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { safeReload } from "@/lib/desktop-chrome-guard";

type Labels = {
  chunkFailure: string;
  retry: string;
  reloadPage: string;
  reloadWaiting: string;
  reloadUnreachable: string;
};

/**
 * Error boundary for lazy-loaded panels (editor, terminal, graph...).
 *
 * Without it, a failed chunk import or a render crash inside a panel unmounts
 * the entire React tree and the user sees a black screen. Here the failure
 * stays inside the panel, with a retry that re-mounts the subtree (which also
 * re-runs a failed dynamic import).
 */
type ReloadState = "idle" | "waiting" | "unreachable";

interface BoundaryState {
  error: Error | null;
  retryKey: number;
  resetKey: string | number | null;
  reload: ReloadState;
}

class Boundary extends Component<
  { labels: Labels; resetKey?: string | number | null; children: ReactNode },
  BoundaryState
> {
  state: BoundaryState = {
    error: null,
    retryKey: 0,
    resetKey: this.props.resetKey ?? null,
    reload: "idle",
  };

  private unmounted = false;

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(
    props: { resetKey?: string | number | null },
    state: BoundaryState,
  ) {
    const next = props.resetKey ?? null;
    if (next === state.resetKey) return null;
    // A different file (or panel) is being shown: one earlier crash must not
    // keep the pane dead forever, which would silently remove the editor and
    // its accept/reject controls with no way back but a full page reload.
    return { error: null, retryKey: state.retryKey + 1, resetKey: next, reload: "idle" };
  }

  componentWillUnmount() {
    this.unmounted = true;
  }

  private retry = () => {
    this.setState((prev) => ({ error: null, retryKey: prev.retryKey + 1, reload: "idle" }));
  };

  // In the desktop shell the reload waits for the engine to answer first, so
  // a restart in progress never surfaces the webview's own error page.
  private reloadInterface = () => {
    if (this.state.reload === "waiting") return;
    this.setState({ reload: "waiting" });
    void safeReload().then((reloaded) => {
      if (this.unmounted || reloaded) return;
      this.setState({ reload: "unreachable" });
    });
  };

  render() {
    if (this.state.error) {
      const chunkFailure =
        /dynamically imported module|Loading chunk|Importing a module script failed/i.test(
          this.state.error.message,
        );
      return (
        <div className="flex h-full min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <TriangleAlert className="h-8 w-8 text-destructive/70" aria-hidden />
          <p className="max-w-md text-[13px] leading-6 text-muted-foreground">
            {chunkFailure ? this.props.labels.chunkFailure : this.state.error.message}
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={this.retry}
              className="flex items-center gap-1.5 rounded-md border border-border/60 bg-background px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-muted/60"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              {this.props.labels.retry}
            </button>
            {chunkFailure ? (
              <button
                type="button"
                onClick={this.reloadInterface}
                disabled={this.state.reload === "waiting"}
                className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-muted/60 disabled:opacity-60"
              >
                {this.state.reload === "waiting"
                  ? this.props.labels.reloadWaiting
                  : this.props.labels.reloadPage}
              </button>
            ) : null}
          </div>
          {this.state.reload === "unreachable" ? (
            <p className="max-w-md text-[12px] leading-5 text-amber-700 dark:text-amber-400" role="status">
              {this.props.labels.reloadUnreachable}
            </p>
          ) : null}
        </div>
      );
    }
    // Changing the key re-creates the whole subtree, so a failed lazy import
    // gets a fresh chance instead of replaying the cached rejection.
    return (
      <div key={this.state.retryKey} className="contents">
        {this.props.children}
      </div>
    );
  }
}

export function PanelErrorBoundary({
  children,
  resetKey,
}: {
  children: ReactNode;
  /** Change this (e.g. the open file path) to clear a previous crash. */
  resetKey?: string | number | null;
}) {
  const { t } = useTranslation();
  return (
    <Boundary
      resetKey={resetKey}
      labels={{
        chunkFailure: t("panelError.chunkFailure", {
          defaultValue:
            "This module could not load (update in progress or network issue). Retry, or restart the interface.",
        }),
        retry: t("panelError.retry", { defaultValue: "Retry" }),
        reloadPage: t("panelError.reloadPage", { defaultValue: "Restart the interface" }),
        reloadWaiting: t("panelError.reloadWaiting", {
          defaultValue: "Waiting for the engine...",
        }),
        reloadUnreachable: t("transport.unreachable", {
          defaultValue:
            "The Navin engine is not reachable right now. It is usually back within a few seconds.",
        }),
      }}
    >
      {children}
    </Boundary>
  );
}
