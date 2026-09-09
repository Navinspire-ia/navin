// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, ChevronRight, CircleDashed, Copy } from "lucide-react";
import { useTranslation } from "react-i18next";

import { MarkdownText, preloadMarkdownText } from "@/components/MarkdownText";
import { copyTextToClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

import {
  REASONING_LABEL_CLASS,
  REASONING_PROSE_CLASS,
  REASONING_RAIL_CLASS,
  mergeReasoningParts,
  reasoningSummary,
} from "./reasoningProse";

export function ReasoningCard({
  parts,
  streaming,
  hasBodyBelow = false,
  duration,
  compact = false,
  onOpenFilePreview,
}: {
  parts: string[];
  streaming: boolean;
  hasBodyBelow?: boolean;
  /** Compact Cursor-style duration next to Thinking, e.g. "1s". */
  duration?: string;
  /** Title line only until opened (the "compact" activity preference). */
  compact?: boolean;
  onOpenFilePreview?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const text = mergeReasoningParts(parts);
  const summary = reasoningSummary(text);
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const copyResetRef = useRef<number | null>(null);

  useEffect(() => {
    if ((open || streaming) && text.length > 0) preloadMarkdownText();
  }, [open, streaming, text.length]);

  useEffect(() => {
    return () => {
      if (copyResetRef.current !== null) window.clearTimeout(copyResetRef.current);
    };
  }, []);

  const onCopy = useCallback((event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (!text.trim()) return;
    void copyTextToClipboard(text).then((ok) => {
      if (!ok) return;
      setCopied(true);
      if (copyResetRef.current !== null) window.clearTimeout(copyResetRef.current);
      copyResetRef.current = window.setTimeout(() => {
        setCopied(false);
        copyResetRef.current = null;
      }, 1_500);
    });
  }, [text]);

  if (!text && !streaming) return null;

  const expandLabel = open
    ? t("message.reasoningCollapse", { defaultValue: "Click to hide" })
    : t("message.reasoningExpand", { defaultValue: "Click to expand" });
  const copyLabel = copied
    ? t("message.copiedReply", { defaultValue: "Copied" })
    : t("message.reasoningCopy", { defaultValue: "Copy thinking" });

  return (
    <div
      className={cn("w-full", hasBodyBelow && "mb-2")}
      data-testid="activity-reasoning-card"
      data-state={streaming ? "streaming" : open ? "expanded" : "collapsed"}
      data-compact={compact || undefined}
    >
      <div className="flex items-start gap-2">
        <ReasoningMarker streaming={streaming} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              className="inline-flex min-w-0 flex-1 items-center gap-1.5 py-0.5 text-left"
              aria-expanded={open}
              aria-live={streaming ? "polite" : undefined}
              data-testid="activity-reasoning-expand"
            >
              <span className={cn("text-[12.5px] font-medium", REASONING_LABEL_CLASS)}>
                {streaming
                  ? t("message.reasoningStreaming", { defaultValue: "Thinking…" })
                  : t("message.reasoning", { defaultValue: "Thinking" })}
              </span>
              {duration && duration !== "0s" ? (
                <span className="text-[11.5px] tabular-nums text-muted-foreground/55">
                  {duration}
                </span>
              ) : null}
              <span className="ml-auto flex shrink-0 items-center gap-1 text-[11.5px] text-muted-foreground/70">
                {expandLabel}
                <ChevronRight
                  aria-hidden
                  className={cn(
                    "h-3.5 w-3.5 transition-transform duration-200",
                    open && "rotate-90",
                  )}
                />
              </span>
            </button>
            <button
              type="button"
              onClick={onCopy}
              disabled={!text.trim()}
              aria-label={copyLabel}
              title={copyLabel}
              data-testid="activity-reasoning-copy"
              className={cn(
                "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
                "text-muted-foreground/70 transition-colors hover:bg-muted/55 hover:text-foreground",
                "disabled:pointer-events-none disabled:opacity-40",
                "active:scale-[0.96] motion-reduce:active:scale-100",
              )}
            >
              {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
            </button>
          </div>
          {!open && !compact && summary ? (
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              className="mt-1.5 block w-full text-left"
            >
              <span className="line-clamp-6 text-[13.5px] leading-6 text-muted-foreground antialiased [text-wrap:pretty]">
                {summary}
              </span>
            </button>
          ) : null}
          <AnimatePresence initial={false}>
            {open && text ? (
              <motion.div
                key="reasoning-body"
                initial={reduceMotion ? false : { opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduceMotion ? undefined : { opacity: 0, y: -4 }}
                transition={{ type: "spring", duration: 0.3, bounce: 0 }}
                className={cn("mt-3 min-w-0", REASONING_RAIL_CLASS)}
                data-testid="activity-reasoning-body"
              >
                <MarkdownText
                  streaming={streaming}
                  onOpenFilePreview={onOpenFilePreview}
                  className={REASONING_PROSE_CLASS}
                >
                  {text}
                </MarkdownText>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

/** @deprecated Use ReasoningCard. Kept so older activity rows still compile. */
export function ReasoningRow({
  text,
  streaming,
  onOpenFilePreview,
}: {
  text: string;
  streaming: boolean;
  onOpenFilePreview?: (path: string) => void;
}) {
  return (
    <ReasoningCard
      parts={[text]}
      streaming={streaming}
      onOpenFilePreview={onOpenFilePreview}
    />
  );
}

function ReasoningMarker({ streaming }: { streaming: boolean }) {
  const wasStreamingRef = useRef(streaming);
  const [justCompleted, setJustCompleted] = useState(false);

  useEffect(() => {
    if (wasStreamingRef.current && !streaming) {
      setJustCompleted(true);
      const timeout = window.setTimeout(() => setJustCompleted(false), 650);
      wasStreamingRef.current = streaming;
      return () => window.clearTimeout(timeout);
    }
    wasStreamingRef.current = streaming;
    return undefined;
  }, [streaming]);

  if (streaming) {
    return (
      <CircleDashed
        data-testid="activity-reasoning-marker"
        data-state="thinking"
        className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-[hsl(var(--composer-ask))]/70"
        strokeWidth={1.8}
        aria-hidden
      />
    );
  }
  return (
    <span
      data-testid="activity-reasoning-marker"
      data-state="done"
      className={cn(
        "mt-0.5 grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full border border-emerald-500/28 text-emerald-500/78",
        "bg-emerald-500/[0.035] transition-[border-color,background-color,box-shadow,transform] duration-300 ease-out",
        justCompleted
          && "animate-in fade-in-0 zoom-in-75 shadow-[0_0_0_3px_rgba(16,185,129,0.10)] motion-reduce:animate-none",
      )}
      aria-hidden
    >
      <Check
        className={cn(
          "h-2 w-2 stroke-[2.4]",
          justCompleted && "animate-in fade-in-0 zoom-in-50 duration-300 motion-reduce:animate-none",
        )}
      />
    </span>
  );
}
