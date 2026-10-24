// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { KeyRound, Loader2, RotateCw, Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { GitConnectPanel } from "@/components/settings/GitConnectPanel";
import { Button } from "@/components/ui/button";
import type { GitImportState } from "@/hooks/useGitImport";
import { cn } from "@/lib/utils";

/**
 * What an Import Git field shows under itself after a failed clone. A private
 * repository gets a plain sentence and Connect, which opens the sign-in card
 * right here and clones again once it works (Settings stays a secondary
 * link); any other failure stays a readable line.
 */
export function GitImportFeedback({
  state,
  className,
  errorClassName,
  buttonClassName,
}: {
  state: GitImportState;
  className?: string;
  errorClassName?: string;
  buttonClassName?: string;
}) {
  const { t } = useTranslation();
  const { error } = state;
  if (!error) return null;
  if (error.kind === "other") {
    return (
      <p role="alert" className={cn("text-[11.5px] font-medium", errorClassName, className)}>
        {error.message}
      </p>
    );
  }
  return (
    <div className={cn("space-y-1.5", className)} data-testid="git-import-auth">
      <p role="alert" className={cn("text-[11.5px] font-medium", errorClassName)}>
        {t("dev.project.gitAuthRequired", {
          defaultValue: "This repository is private or needs sign-in for {{host}}.",
          host: error.host,
        })}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          type="button"
          variant={state.connectRequested || state.connectOpen ? "outline" : "default"}
          aria-expanded={state.connectOpen}
          onClick={state.connect}
          className={cn("h-8 rounded-lg px-2.5 text-[11.5px]", buttonClassName)}
        >
          <KeyRound className="me-1 h-3.5 w-3.5" aria-hidden />
          {t("dev.project.gitAuthConnect", {
            defaultValue: "Connect {{host}}",
            host: error.host,
          })}
        </Button>
        {state.connectRequested ? (
          <Button
            type="button"
            disabled={state.cloning}
            onClick={() => void state.importRepo()}
            className={cn("h-8 rounded-lg px-2.5 text-[11.5px]", buttonClassName)}
          >
            {state.cloning ? (
              <Loader2 className="me-1 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RotateCw className="me-1 h-3.5 w-3.5" aria-hidden />
            )}
            {t("dev.project.gitAuthRetry", { defaultValue: "Retry" })}
          </Button>
        ) : null}
        <button
          type="button"
          onClick={state.openSettings}
          className="inline-flex items-center gap-1 px-1 text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          <Settings2 className="h-3 w-3" aria-hidden />
          {t("dev.project.gitAuthOpenSettings", { defaultValue: "Open settings" })}
        </button>
      </div>
      {state.connectOpen ? (
        <GitConnectPanel
          compact
          token={state.token}
          host={error.host}
          onConnected={() => void state.onConnected()}
          className="text-start"
        />
      ) : null}
    </div>
  );
}
