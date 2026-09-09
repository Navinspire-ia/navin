// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { FreeSetupSteps, useFreeSetup } from "@/components/onboarding/FreeSetup";
import {
  OmniRouteSetupSteps,
  useOmniRouteSetup,
} from "@/components/onboarding/OmniRouteSetup";
import { OllamaSetupPanel } from "@/components/settings/OllamaSetupPanel";
import {
  FREE_ONBOARDING_PATHS,
  markOnboardingComplete,
  type OnboardingPath,
  type OnboardingState,
} from "@/lib/onboarding";
import type { SettingsPayload } from "@/lib/types";
import { supportedLocales } from "@/i18n/config";
import { cn } from "@/lib/utils";

type Step = 1 | 2 | 3;

/** Step-2 sub-flows rendered in place of the path cards. */
type Stage = "" | "free" | "omniroute" | "ollama";

export type FirstRunWizardProps = {
  open: boolean;
  onComplete: (state: OnboardingState) => void;
  onOpenAccount: () => void;
  onOpenProviders: () => void;
  onOpenDemo: () => void;
  /**
   * The Free paths connect an account or write a local provider inside the
   * wizard; the parent must not auto-complete onboarding mid-flow.
   */
  onFreeStageChange?: (active: boolean) => void;
  /** Fired when a provider just became usable during a Free setup. */
  onWorkspaceSynced?: () => void;
  /** Gateway API token - persists completion under ~/.navin. */
  token?: string;
  className?: string;
};

/** Only locales that exist under webui/src/i18n/locales (en + fr today). */
const LANGS = supportedLocales.map((locale) => ({
  code: locale.code,
  label: locale.nativeLabel,
}));

function stageFor(path: OnboardingPath | undefined): Stage {
  return path === "free" || path === "omniroute" || path === "ollama" ? path : "";
}

export function FirstRunWizard({
  open,
  onComplete,
  onOpenAccount,
  onOpenProviders,
  onOpenDemo,
  onFreeStageChange,
  onWorkspaceSynced,
  token,
  className,
}: FirstRunWizardProps) {
  const { t, i18n } = useTranslation();
  const [step, setStep] = useState<Step>(1);
  const [language, setLanguage] = useState(i18n.language?.slice(0, 2) || "en");
  const [path, setPath] = useState<OnboardingState["path"]>(undefined);
  const [stage, setStage] = useState<Stage>("");
  const freeSetup = useFreeSetup(open && stage === "free", token, onWorkspaceSynced);
  const omniRouteSetup = useOmniRouteSetup(
    open && stage === "omniroute",
    token,
    onWorkspaceSynced,
  );
  // The Ollama panel reports back the settings it wrote; "done" once the
  // provider row is configured (endpoint pinned, optionally a model).
  const [ollamaDone, setOllamaDone] = useState(false);

  // Suppress the parent's "install already configured" auto-complete for the
  // whole Free journey: the navin account connects (or a local provider is
  // written) mid-flow, which would otherwise close the wizard early.
  useEffect(() => {
    const freePath = path !== undefined && FREE_ONBOARDING_PATHS.includes(path);
    onFreeStageChange?.((stage !== "" || freePath) && open);
  }, [onFreeStageChange, open, path, stage]);

  const finish = useCallback(
    (chosen: OnboardingState["path"]) => {
      const state = markOnboardingComplete(
        {
          language,
          path: chosen ?? "skip",
          demoOpened: chosen !== undefined && chosen !== "skip",
        },
        token,
      );
      onComplete(state);
    },
    [language, onComplete, token],
  );

  const onOllamaConfigured = useCallback(
    (settings: SettingsPayload) => {
      const row = (settings.providers ?? []).find((provider) => provider.name === "ollama");
      if (row?.configured) {
        setOllamaDone(true);
        onWorkspaceSynced?.();
      }
    },
    [onWorkspaceSynced],
  );

  const stepLabel = useMemo(
    () => t("onboarding.wizard.stepOf", { current: step, total: 3 }),
    [step, t],
  );

  if (!open) return null;

  const freeBadge = t("onboarding.wizard.freeBadge");
  const pathCards: Array<{
    id: NonNullable<OnboardingState["path"]>;
    title: string;
    body: string;
    badge?: string;
  }> = [
    {
      id: "byok",
      title: t("onboarding.wizard.byokTitle"),
      body: t("onboarding.wizard.byokBody"),
    },
    {
      id: "free",
      title: t("onboarding.wizard.freeTitle"),
      body: t("onboarding.wizard.freeBody"),
      badge: freeBadge,
    },
    {
      id: "omniroute",
      title: t("onboarding.wizard.omnirouteTitle"),
      body: t("onboarding.wizard.omnirouteBody"),
      badge: freeBadge,
    },
    {
      id: "ollama",
      title: t("onboarding.wizard.ollamaTitle"),
      body: t("onboarding.wizard.ollamaBody"),
      badge: freeBadge,
    },
    {
      id: "account",
      title: t("onboarding.wizard.accountTitle"),
      body: t("onboarding.wizard.accountBody"),
    },
  ];

  const stageDone =
    stage === "free"
      ? freeSetup.done
      : stage === "omniroute"
        ? omniRouteSetup.done
        : stage === "ollama"
          ? ollamaDone
          : true;

  const heading =
    step === 2 && stage
      ? t(`onboarding.wizard.${stage}SetupTitle`)
      : t(`onboarding.wizard.step${step}.title`);
  const body =
    step === 2 && stage
      ? t(`onboarding.wizard.${stage}SetupBody`)
      : t(`onboarding.wizard.step${step}.body`);

  return (
    <div
      className={cn(
        "fixed inset-0 z-[80] flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm",
        className,
      )}
      role="dialog"
      aria-modal="true"
      aria-labelledby="first-run-wizard-title"
    >
      <div className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-card p-6 shadow-xl">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {stepLabel}
        </p>
        <h2
          id="first-run-wizard-title"
          className="mt-2 text-2xl font-semibold tracking-tight text-foreground"
        >
          {heading}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">{body}</p>

        {step === 1 ? (
          <div className="mt-6 grid gap-2">
            {LANGS.map((lang) => (
              <button
                key={lang.code}
                type="button"
                className={cn(
                  "rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                  language === lang.code
                    ? "border-primary bg-primary/10 text-foreground"
                    : "border-border hover:bg-muted/60",
                )}
                onClick={() => {
                  setLanguage(lang.code);
                  void i18n.changeLanguage(lang.code);
                }}
              >
                {lang.label}
              </button>
            ))}
          </div>
        ) : null}

        {step === 2 && !stage ? (
          <div className="mt-6 grid gap-3">
            {pathCards.map((card) => (
              <button
                key={card.id}
                type="button"
                data-path={card.id}
                className={cn(
                  "rounded-xl border px-4 py-3 text-left transition-colors",
                  path === card.id
                    ? "border-primary bg-primary/10"
                    : "border-border hover:bg-muted/60",
                )}
                onClick={() => setPath(card.id)}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{card.title}</span>
                  {card.badge ? (
                    <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">
                      {card.badge}
                    </span>
                  ) : null}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">{card.body}</div>
              </button>
            ))}
          </div>
        ) : null}

        {step === 2 && stage === "free" ? (
          <div className="mt-6">
            <FreeSetupSteps state={freeSetup} />
          </div>
        ) : null}

        {step === 2 && stage === "omniroute" ? (
          <div className="mt-6">
            <OmniRouteSetupSteps state={omniRouteSetup} />
          </div>
        ) : null}

        {step === 2 && stage === "ollama" ? (
          <div className="mt-6 grid gap-3">
            {token ? (
              <OllamaSetupPanel token={token} onConfigured={onOllamaConfigured} />
            ) : (
              <Button type="button" variant="outline" onClick={onOpenProviders}>
                {t("onboarding.wizard.ollamaOpenSettings")}
              </Button>
            )}
            <p className="text-xs text-muted-foreground">
              {t("onboarding.wizard.ollamaHint")}
            </p>
          </div>
        ) : null}

        {step === 3 ? (
          <div className="mt-6 grid gap-2">
            <Button
              type="button"
              variant="default"
              className="justify-start"
              onClick={() => {
                onOpenDemo();
                finish(path ?? "skip");
              }}
            >
              {t("onboarding.wizard.openDemo")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="justify-start"
              onClick={() => finish(path ?? "skip")}
            >
              {t("onboarding.wizard.startChatting")}
            </Button>
          </div>
        ) : null}

        <div className="mt-8 flex items-center justify-between gap-3">
          {step > 1 ? (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                if (step === 2 && stage) {
                  setStage("");
                  return;
                }
                setStep((s) => (s - 1) as Step);
              }}
            >
              {t("onboarding.wizard.back")}
            </Button>
          ) : (
            <Button
              type="button"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => finish("skip")}
            >
              {t("onboarding.wizard.skip", { defaultValue: "Skip" })}
            </Button>
          )}
          <div className="flex items-center gap-2">
            {step > 1 && step < 3 ? (
              <Button
                type="button"
                variant="ghost"
                className="text-muted-foreground"
                onClick={() => finish("skip")}
              >
                {t("onboarding.wizard.skip", { defaultValue: "Skip" })}
              </Button>
            ) : null}
            {step < 3 ? (
              <Button
                type="button"
                disabled={
                  (step === 2 && !stage && !path)
                  || (step === 2 && stage !== "" && !stageDone)
                }
                onClick={() => {
                  if (step === 2 && !stage && stageFor(path)) {
                    setStage(stageFor(path));
                    return;
                  }
                  if (step === 2 && path === "account") onOpenAccount();
                  if (step === 2 && path === "byok") onOpenProviders();
                  setStep((s) => (s + 1) as Step);
                }}
              >
                {t("onboarding.wizard.next")}
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
