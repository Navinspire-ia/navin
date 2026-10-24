// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AppWindow, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useRunningSite } from "@/lib/running-site";
import { SHARE_SITE_EVENT } from "@/lib/share-events";

const DISMISSED_KEY = "navin.appShareHint.dismissed";

function readDismissed(): string[] {
  try {
    const raw = JSON.parse(sessionStorage.getItem(DISMISSED_KEY) || "[]");
    return Array.isArray(raw) ? raw.map(String) : [];
  } catch {
    return [];
  }
}

/**
 * Once the chat's app runs, a quiet line above the composer offers a link to
 * it (AppShareDialog). Closed, it stays closed for that app this session.
 */
export function AppShareHint({ chatId }: { chatId: string }) {
  const { t } = useTranslation();
  const site = useRunningSite(chatId);
  const [dismissed, setDismissed] = useState(readDismissed);
  if (!site) return null;
  const key = `${chatId}|${site}`;
  if (dismissed.includes(key)) return null;

  const dismiss = () => {
    const next = [...dismissed, key].slice(-50);
    setDismissed(next);
    try {
      sessionStorage.setItem(DISMISSED_KEY, JSON.stringify(next));
    } catch {
      // Private mode: closed for this view only.
    }
  };

  return (
    <div className="mb-2 flex items-center gap-2 rounded-lg border border-sky-500/25 bg-sky-500/[0.06] px-3 py-2 text-[12.5px]">
      <AppWindow className="h-4 w-4 shrink-0 text-sky-600 dark:text-sky-400" aria-hidden />
      <span className="min-w-0 flex-1 text-foreground/85">
        {t("chat.liveShare.appHint.text", {
          defaultValue: "Your app is running. Want to show it? Share a link: the conversation stays private.",
        })}
      </span>
      <Button
        size="sm"
        variant="outline"
        className="h-7 shrink-0"
        onClick={() => window.dispatchEvent(new CustomEvent(SHARE_SITE_EVENT, { detail: { chatId, url: site } }))}
      >
        {t("chat.liveShare.appHint.action", { defaultValue: "Share the application" })}
      </Button>
      <button
        type="button"
        onClick={dismiss}
        aria-label={t("common.close", { defaultValue: "Close" })}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}
