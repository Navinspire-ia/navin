// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SettingsViewProps } from "@/components/settings/SettingsView";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { lazyWithRetry } from "@/lib/lazy-retry";
import type { SettingsDialogSection } from "@/lib/settings-events";

// The settings screens stay in their own chunk: the window loads them on first open.
const SettingsView = lazyWithRetry(() =>
  import("@/components/settings/SettingsView").then((m) => ({ default: m.SettingsView })),
);

const SECTION_TITLES: Record<SettingsDialogSection, [string, string]> = {
  channels: ["settings.nav.channels", "Channels"],
  team: ["settings.nav.team", "Team"],
};

export type SettingsSectionDialogProps = Omit<
  SettingsViewProps,
  "initialSection" | "showSidebar" | "embedded" | "onBackToChat" | "onSectionChange" | "hostChromeInset" | "onToggleSidebar"
> & {
  /** The section on screen, or null when the window is closed. */
  section: SettingsDialogSection | null;
  onClose: () => void;
};

/**
 * Channels and app connectors, or the team, in a window over the chat or Code:
 * the same settings screen as the Settings page (every card, sign-in and form
 * works the same), without leaving the conversation.
 */
export function SettingsSectionDialog({ section, onClose, ...settingsProps }: SettingsSectionDialogProps) {
  const { t } = useTranslation();
  const [titleKey, titleDefault] = SECTION_TITLES[section ?? "channels"];
  return (
    <Dialog
      open={section !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent
        data-testid="settings-section-dialog"
        data-section={section ?? undefined}
        className="flex h-[min(90dvh,60rem)] w-[min(78rem,calc(100vw-2rem))] max-w-none flex-col gap-0 overflow-hidden rounded-[22px] p-0 sm:rounded-[22px]"
      >
        <DialogTitle className="sr-only">{t(titleKey, { defaultValue: titleDefault })}</DialogTitle>
        <DialogDescription className="sr-only">
          {t("settings.dialog.description", {
            defaultValue: "Changes are saved right away. Close this window to go back to your work.",
          })}
        </DialogDescription>
        {section ? (
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("settings.status.loading", { defaultValue: "Loading settings" })}
              </div>
            }
          >
            <SettingsView
              {...settingsProps}
              key={section}
              initialSection={section}
              showSidebar={false}
              embedded
              onBackToChat={onClose}
            />
          </Suspense>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
