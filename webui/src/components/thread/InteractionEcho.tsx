// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { memo } from "react";
import { CornerDownRight, MessageCircleQuestion, ShieldCheck, ShieldX } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import type { UIInteractionEcho } from "@/lib/types";

export function choiceEchoAnswer(echo: UIInteractionEcho, t: TFunction): string {
  const label = echo.label ?? "";
  if (echo.customText) return echo.customText;
  if (echo.stopped) {
    return t("thread.choiceEcho.stopped", { defaultValue: "Stopped before an answer" });
  }
  if (echo.timedOut) {
    return t("thread.choiceEcho.timedOut", {
      label,
      defaultValue: "No answer in time - used recommended: {{label}}",
    });
  }
  if (echo.skipped) {
    return t("thread.choiceEcho.skipped", {
      label,
      defaultValue: "Skipped - used recommended: {{label}}",
    });
  }
  return label;
}

/**
 * A settled ask_user card or approval, restated where it happened so the
 * exchange reads in the chat after the card above the composer is gone.
 */
export const InteractionEcho = memo(function InteractionEcho({ echo }: { echo: UIInteractionEcho }) {
  const { t } = useTranslation();

  if (echo.type === "approval") {
    const Icon = echo.allowed ? ShieldCheck : ShieldX;
    const text = echo.allowed
      ? t("thread.approvalEcho.allowed", { action: echo.question, defaultValue: "Allowed: {{action}}" })
      : t("thread.approvalEcho.denied", { action: echo.question, defaultValue: "Denied: {{action}}" });
    return (
      <div
        data-testid="approval-echo"
        className="flex min-w-0 items-center gap-2 text-[14px] leading-6 text-muted-foreground"
      >
        <Icon
          className={echo.allowed ? "h-4 w-4 shrink-0 text-emerald-600/80 dark:text-emerald-400/80" : "h-4 w-4 shrink-0 text-amber-600/80 dark:text-amber-400/80"}
          aria-hidden
        />
        <span className="min-w-0 truncate" title={text}>{text}</span>
      </div>
    );
  }

  const answer = choiceEchoAnswer(echo, t);
  return (
    <div
      data-testid="choice-echo"
      className="w-full min-w-0 max-w-full rounded-xl border border-border/60 bg-muted/30 px-3 py-2 text-[14px] leading-6"
    >
      <div className="flex min-w-0 items-start gap-2 text-muted-foreground">
        <MessageCircleQuestion className="mt-1 h-4 w-4 shrink-0 text-primary/70" aria-hidden />
        <p className="min-w-0 flex-1 break-words">{echo.question}</p>
      </div>
      <div className="mt-0.5 flex min-w-0 items-start gap-2 ps-6">
        <CornerDownRight className="rtl:-scale-x-100 mt-1 h-4 w-4 shrink-0 text-muted-foreground/70" aria-hidden />
        <p
          data-testid="choice-echo-answer"
          className="min-w-0 flex-1 whitespace-pre-wrap break-words font-medium text-foreground"
        >
          {answer}
        </p>
      </div>
    </div>
  );
});
