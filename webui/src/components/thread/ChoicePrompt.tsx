import { useCallback, useEffect, useRef, useState } from "react";
import { MessageCircleQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useClient } from "@/providers/ClientProvider";
import { CUSTOM_CHOICE_MAX_CHARS, OTHER_CHOICE_ID, choiceAnswerPayload, choiceCanContinue, type PendingChoice } from "@/lib/choices";
import { cn } from "@/lib/utils";
import type { ConnectionStatus } from "@/lib/types";

interface ChoicePromptProps {
  request: PendingChoice;
  onRespond: (requestId: string, optionId: string, skipped?: boolean, customText?: string) => void;
}

/**
 * The agent stopped because the next step is a real fork.
 *
 * Unlike an approval, this is not allow/refuse. It is A/B/C plus a last
 * option where the user types their own answer, so Navin's list is never
 * the only way through.
 */
export function ChoicePrompt({ request, onRespond }: ChoicePromptProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const [selected, setSelected] = useState(
    () => request.recommendedId || request.options[0]?.id || "",
  );
  const [customText, setCustomText] = useState("");
  const [answered, setAnswered] = useState(false);
  const [status, setStatus] = useState<ConnectionStatus>(client.status);
  const customRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => client.onStatus(setStatus), [client]);
  const connected = status === "open";

  useEffect(() => {
    setAnswered(false);
    setCustomText("");
    setSelected(request.recommendedId || request.options[0]?.id || "");
  }, [request]);

  const otherSelected = selected === OTHER_CHOICE_ID;
  const busy = answered || !connected;
  const canContinue = choiceCanContinue(selected, customText);

  const continueChoice = () => {
    if (busy || !canContinue) return;
    const payload = choiceAnswerPayload(selected, false, customText);
    if (payload.optionId === OTHER_CHOICE_ID && !payload.customText) return;
    setAnswered(true);
    onRespond(request.requestId, payload.optionId, false, payload.customText);
  };
  const skip = useCallback(() => {
    if (busy) return;
    setAnswered(true);
    onRespond(request.requestId, request.recommendedId || selected, true);
  }, [busy, onRespond, request.recommendedId, request.requestId, selected]);

  useEffect(() => {
    if (!request.allowSkip) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // Typing a custom answer: Escape should not skip the whole card.
      if (event.target instanceof HTMLTextAreaElement) return;
      event.preventDefault();
      skip();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [request.allowSkip, skip]);

  const selectOther = () => {
    setSelected(OTHER_CHOICE_ID);
    window.requestAnimationFrame(() => customRef.current?.focus());
  };

  const otherLetter = String.fromCharCode(65 + request.options.length);

  return (
    <div
      role="dialog"
      aria-labelledby={`choice-title-${request.requestId}`}
      data-choice-prompt-scroll
      className={cn(
        "mb-2 max-h-[min(52vh,32rem)] overflow-y-auto overscroll-contain rounded-lg border border-border/80 bg-card",
        "px-3 py-2.5 text-[12px] leading-5",
        "animate-in fade-in-0 slide-in-from-bottom-1",
      )}
    >
      <div className="flex items-start gap-2">
        <MessageCircleQuestion
          className="mt-0.5 h-4 w-4 shrink-0 text-primary"
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("thread.choice.title", "Questions")}
          </p>
          <p
            id={`choice-title-${request.requestId}`}
            className="mt-0.5 font-medium text-foreground"
          >
            {request.question}
          </p>
          <div className="mt-2 flex flex-col gap-1.5" role="radiogroup">
            {request.options.map((option, index) => {
              const active = selected === option.id;
              const letter = String.fromCharCode(65 + index);
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={busy}
                  onClick={() => setSelected(option.id)}
                  className={cn(
                    "rounded-md border px-2.5 py-2 text-left transition-colors",
                    active
                      ? "border-primary/50 bg-primary/10"
                      : "border-border/70 bg-background hover:border-border",
                  )}
                >
                  <div className="flex items-start gap-2">
                    <span className="mt-px w-4 shrink-0 font-semibold text-muted-foreground">
                      {letter}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-foreground">
                        {option.label}
                        {option.recommended ? (
                          <span className="ml-1.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                            {t("thread.choice.recommended", "Recommended")}
                          </span>
                        ) : null}
                      </p>
                      {option.detail ? (
                        <p className="mt-0.5 text-muted-foreground">{option.detail}</p>
                      ) : null}
                    </div>
                  </div>
                </button>
              );
            })}
            <div
              className={cn(
                "rounded-md border px-2.5 py-2 text-left transition-colors",
                otherSelected
                  ? "border-primary/50 bg-primary/10"
                  : "border-border/70 bg-background hover:border-border",
              )}
            >
              <button
                type="button"
                role="radio"
                aria-checked={otherSelected}
                disabled={busy}
                onClick={selectOther}
                className="flex w-full items-start gap-2 text-left"
              >
                <span className="mt-px w-4 shrink-0 font-semibold text-muted-foreground">
                  {otherLetter}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-foreground">
                    {t("thread.choice.other", "Other")}
                  </p>
                  <p className="mt-0.5 text-muted-foreground">
                    {t(
                      "thread.choice.otherDetail",
                      "Type your own answer. Navin will follow what you write.",
                    )}
                  </p>
                </div>
              </button>
              <textarea
                ref={customRef}
                value={customText}
                disabled={busy}
                maxLength={CUSTOM_CHOICE_MAX_CHARS}
                rows={3}
                onFocus={selectOther}
                onChange={(event) => {
                  setSelected(OTHER_CHOICE_ID);
                  setCustomText(event.target.value);
                }}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                    event.preventDefault();
                    continueChoice();
                  }
                }}
                placeholder={t("thread.choice.otherPlaceholder", "Your answer")}
                aria-label={t("thread.choice.other", "Other")}
                className="mt-2 w-full resize-y rounded-md border border-border/70 bg-background px-2 py-1.5 text-[12px] leading-5 text-foreground outline-none placeholder:text-muted-foreground focus:border-primary/50"
              />
            </div>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {request.allowSkip ? (
              <Button size="sm" variant="ghost" disabled={busy} onClick={skip}>
                {t("thread.choice.skip", "Skip Esc")}
              </Button>
            ) : null}
            <Button size="sm" className="ml-auto" disabled={busy || !canContinue} onClick={continueChoice}>
              {t("thread.choice.continue", "Continue")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}