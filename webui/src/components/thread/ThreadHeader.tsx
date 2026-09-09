// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Menu } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ThreadHeaderProps {
  title: string;
  onToggleSidebar: () => void;
  hideSidebarToggleForHostChrome?: boolean;
  hostChromeTitleInset?: boolean;
  minimal?: boolean;
  promptNavigatorAction?: ReactNode;
  sessionInfoAction?: ReactNode;
  leadingActions?: ReactNode;
  presenceAction?: ReactNode;
  findAction?: ReactNode;
}

export function ThreadHeader({
  title,
  onToggleSidebar,
  hideSidebarToggleForHostChrome = false,
  hostChromeTitleInset = false,
  minimal = false,
  promptNavigatorAction,
  sessionInfoAction,
  leadingActions,
  presenceAction,
  findAction,
}: ThreadHeaderProps) {
  const { t } = useTranslation();

  return (
    <div
      className={cn(
        "relative z-10 flex items-center justify-between gap-3 border-b border-border/60 bg-background/80 py-2.5 pl-3 backdrop-blur-md",
        // Fixed notification bell sits over the top-right; keep session/prompt
        // controls clear of it (same gutter as workbench / file preview).
        NOTIFICATION_GUTTER,
        minimal && "h-11",
        !minimal && hostChromeTitleInset && "lg:pl-[128px]",
      )}
    >
      <div className="relative flex min-w-0 items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("thread.header.toggleSidebar")}
          onClick={onToggleSidebar}
          className={cn(
            "h-8 w-8 rounded-xl text-muted-foreground hover:bg-accent hover:text-foreground",
            hideSidebarToggleForHostChrome && "lg:hidden",
          )}
        >
          <Menu className="h-4 w-4" />
        </Button>
        {leadingActions}
        {!minimal ? (
          <div className="flex min-w-0 items-center rounded-lg px-2 py-1 text-[13px] font-medium tracking-tight text-foreground/80">
            <span className="max-w-[min(60vw,32rem)] truncate">{title}</span>
          </div>
        ) : null}
        {presenceAction}
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-1">
        {findAction}
        {sessionInfoAction}
        {promptNavigatorAction}
      </div>

      {!minimal ? (
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-full h-4" />
      ) : null}
    </div>
  );
}

