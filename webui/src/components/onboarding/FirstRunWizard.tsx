// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { markOnboardingComplete, type OnboardingState } from "@/lib/onboarding";
import { setAppLanguage } from "@/i18n";
import { isRtlLocale, supportedLocales } from "@/i18n/config";
import { cn } from "@/lib/utils";

export type FirstRunWizardProps = {
  open: boolean;
  onComplete: (state: OnboardingState) => void;
  /** Gateway API token - persists completion under ~/.navin. */
  token?: string;
  className?: string;
};

/** The languages the app ships today (en, fr, ar). */
const LANGS = supportedLocales.map((locale) => ({
  code: locale.code,
  label: locale.nativeLabel,
}));

/**
 * First run: the language, and nothing else. Getting a model (configure a
 * provider or subscribe to Navin) happens afterwards, from the bar the app
 * shows until one is ready.
 */
export function FirstRunWizard({ open, onComplete, token, className }: FirstRunWizardProps) {
  const { t, i18n } = useTranslation();
  const [language, setLanguage] = useState(i18n.language?.slice(0, 2) || "en");

  const finish = useCallback(() => {
    const state = markOnboardingComplete({ language, path: "skip" }, token);
    onComplete(state);
  }, [language, onComplete, token]);

  if (!open) return null;

  return (
    <div
      className={cn(
        "host-no-drag fixed inset-0 z-[80] overflow-y-auto overscroll-contain bg-background/80 p-3 backdrop-blur-sm sm:p-4",
        className,
      )}
      data-testid="first-run-wizard"
      role="dialog"
      aria-modal="true"
      aria-labelledby="first-run-wizard-title"
    >
      <div className="flex min-h-full items-center justify-center py-2">
        <div className="flex max-h-[min(92dvh,calc(100dvh-1.5rem))] w-full max-w-md min-h-0 flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-xl">
          <div className="shrink-0 px-6 pt-6">
            <h2
              id="first-run-wizard-title"
              className="text-2xl font-semibold tracking-tight text-foreground"
            >
              {t("onboarding.wizard.step1.title")}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">{t("onboarding.wizard.step1.body")}</p>
          </div>
          <div
            data-testid="first-run-wizard-scroll"
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-6"
          >
            <div className="mt-6 grid gap-2 pb-2" role="radiogroup">
              {LANGS.map((lang) => (
                <button
                  key={lang.code}
                  type="button"
                  role="radio"
                  aria-checked={language === lang.code}
                  lang={lang.code}
                  dir={isRtlLocale(lang.code) ? "rtl" : "ltr"}
                  data-testid={`first-run-language-${lang.code}`}
                  className={cn(
                    "rounded-xl border px-4 py-3 text-start text-sm transition-colors",
                    language === lang.code
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border hover:bg-muted/60",
                  )}
                  onClick={() => {
                    setLanguage(lang.code);
                    void setAppLanguage(lang.code);
                  }}
                >
                  {lang.label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex shrink-0 items-center justify-end gap-3 border-t border-border/60 px-6 py-4">
            <Button type="button" onClick={finish} data-testid="first-run-continue">
              {t("onboarding.wizard.next")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
