// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useTranslation } from "react-i18next";

import { PlatformLogo } from "@/components/dev/PlatformLogo";
import type { AppFactoryTarget } from "@/components/dev/appFactoryTargets";

/**
 * The user's "/apps android" turn, shown as what it asked for: the platform
 * mark, "Create the Android app", the package and the folder it goes to.
 */
export function AppFactoryRequestCard({ target }: { target: AppFactoryTarget }) {
  const { t } = useTranslation();
  return (
    <div
      dir="auto"
      data-testid="app-factory-request"
      className="flex w-fit max-w-full items-center gap-3 rounded-[16px] bg-secondary/70 px-3.5 py-2.5 text-start"
    >
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-background/80 ring-1 ring-border/60">
        <PlatformLogo id={target.id} className="h-5 w-5" />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[14px] font-medium text-foreground">
          {t("dev.appFactory.confirm.start", {
            platform: target.label,
            defaultValue: "Create the {{platform}} app",
          })}
        </span>
        <span className="block truncate text-[12px] text-muted-foreground">
          {target.deliverables} · <span className="font-mono">{target.folder}</span>
        </span>
      </span>
    </div>
  );
}
