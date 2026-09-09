import { useCallback, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
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

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

function optionTitle(label: string, detail: string): string {
  return detail ? `${label} - ${detail}` : label;
}

/**
 * The agent stopped because the next step is a real fork.
 *
 * Compact chat-width card: one line per option (label then subtitle), no wrap,
 * so it stays inside the composer column instead of filling the pane.
 */
export function ChoicePrompt({ request, onRespond }: ChoicePromptProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const reduceMotion = useReducedMotion();
  const [selected, setSelected] = useState(
    () => request.recommendedId || request.options[0]?.id || "",
  );
  const [customText, setCustomText] = useState("");
  const [answered, setAnswered] = useState(false);
  const [status, setStatus] = useState<ConnectionStatus>(client.status);
  const customRef = useRef<HTMLInputElement>(null);

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
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
        return;
      }
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
  const otherDetail = t(
    "thread.choice.otherDetail",
    "Type your own answer. Navin will follow what you write.",
  );

  const rowClass = (active: boolean) =>
    cn(
      "flex h-7 w-full min-w-0 items-center gap-1.5 rounded-md border px-2 text-left text-[12px] leading-4",
      "transition-[border-color,background-color]",
      active
        ? "border-primary/50 bg-primary/10"
        : "border-transparent hover:border-border/70 hover:bg-background/80",
    );

  return (
    <div
      role="dialog"
      aria-labelledby={`choice-title-${request.requestId}`}
      data-choice-prompt-scroll
      className={cn(
        "mb-2 w-full min-w-0 max-w-full overflow-hidden rounded-xl border border-border/70 bg-card/95",
        "px-1.5 py-1.5 text-[12px]",
      )}
    >
      <div className="flex min-w-0 items-center gap-2 px-1">
        <MessageCircleQuestion
          className="h-3.5 w-3.5 shrink-0 text-primary"
          aria-hidden
        />
        <p className="shrink-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {t("thread.choice.title", "Questions")}
        </p>
        <p
          id={`choice-title-${request.requestId}`}
          className="min-w-0 flex-1 truncate font-medium text-foreground"
          title={request.question}
        >
          {request.question}
        </p>
        <div className="flex shrink-0 items-center gap-1">
          {request.allowSkip ? (
            <Button size="sm" variant="ghost" className="h-7 px-2" disabled={busy} onClick={skip}>
              {t("thread.choice.skip", "Skip Esc")}
            </Button>
          ) : null}
          <Button size="sm" className="h-7 px-2.5" disabled={busy || !canContinue} onClick={continueChoice}>
            {t("thread.choice.continue", "Continue")}
          </Button>
        </div>
      </div>
      <div className="mt-1 flex min-w-0 flex-col gap-0.5" role="radiogroup">
        {request.options.map((option, index) => {
          const active = selected === option.id;
          const letter = String.fromCharCode(65 + index);
          const title = optionTitle(option.label, option.detail);
          return (
            <motion.button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={busy}
              title={title}
              onClick={() => setSelected(option.id)}
              className={rowClass(active)}
              whileTap={reduceMotion ? undefined : { scale: 0.96 }}
              transition={spring}
            >
              <span className="w-3.5 shrink-0 text-[11px] font-semibold tabular-nums text-muted-foreground">
                {letter}
              </span>
              <span className="min-w-0 flex-1 truncate">
                <span className="font-medium text-foreground">{option.label}</span>
                {option.detail ? (
                  <span className="text-muted-foreground">
                    {" "}
                    · {option.detail}
                  </span>
                ) : null}
              </span>
              {option.recommended ? (
                <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-primary">
                  {t("thread.choice.recommended", "Recommended")}
                </span>
              ) : null}
            </motion.button>
          );
        })}
        <motion.div
          className={rowClass(otherSelected)}
          whileTap={reduceMotion ? undefined : { scale: 0.96 }}
          transition={spring}
        >
          <button
            type="button"
            role="radio"
            aria-checked={otherSelected}
            disabled={busy}
            title={optionTitle(t("thread.choice.other", "Other"), otherDetail)}
            onClick={selectOther}
            className="flex min-w-0 shrink-0 items-center gap-1.5 text-left"
          >
            <span className="w-3.5 shrink-0 text-[11px] font-semibold tabular-nums text-muted-foreground">
              {otherLetter}
            </span>
            <span className="font-medium text-foreground">
              {t("thread.choice.other", "Other")}
            </span>
          </button>
          <input
            ref={customRef}
            value={customText}
            disabled={busy}
            maxLength={CUSTOM_CHOICE_MAX_CHARS}
            onFocus={selectOther}
            onChange={(event) => {
              setSelected(OTHER_CHOICE_ID);
              setCustomText(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                continueChoice();
              }
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                continueChoice();
              }
            }}
            placeholder={t("thread.choice.otherPlaceholder", "Your answer")}
            aria-label={t("thread.choice.other", "Other")}
            className="h-5 min-w-0 flex-1 bg-transparent text-[12px] leading-5 text-foreground outline-none placeholder:text-muted-foreground"
          />
        </motion.div>
      </div>
    </div>
  );
}
